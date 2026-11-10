"""Manual billing and destructive legacy-provider removal, using fictional data only."""

from datetime import datetime, timedelta
from pathlib import Path
import json, sqlite3
import pytest
from test_plan import site, create_cycle, post_purchase
from test_customer_payments import payments, package, staff, admin_form, deliver
from test_advance_payments import customer, advance
from test_app import token
from core import TZ
import payment_policies as policies
from scripts.remove_fast_oracle import remove_environment


@pytest.mark.parametrize("method", ["Pix AI", "Dinheiro"])
def test_manual_subscription_renewal_and_credits_no_network(
    payments, monkeypatch, method
):
    app, client, db, _ = payments
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *a, **k: pytest.fail("No remote billing request is permitted"),
    )
    for scope in policies.SCOPES:
        rules = policies.rules(db, scope)
        for r in rules:
            r["priority"] = 1 if r["method"] == method else 2
        db.execute(
            "UPDATE customer_payment_policies SET rules=? WHERE scope=?",
            (json.dumps(rules), scope),
        )
    db.commit()
    for renewal in (False, True):
        customer(client)
        url = post_purchase(client).location
        order = db.execute(
            "SELECT * FROM plan_orders ORDER BY created_at DESC"
        ).fetchone()
        assert order["local_method"] == method and order["state"] == "pending"
        assert post_purchase(client).location == url
        assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == int(
            renewal
        )
        staff(client)
        path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/approve"
        fields = admin_form(client, path, method)
        assert client.post(path, data=fields).status_code == 302
        assert client.post(path, data=fields).status_code == 302
        assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()[
            "n"
        ] == 1 + int(renewal)
    current, future = list(db.execute("SELECT * FROM plan_cycles ORDER BY starts_at"))
    assert (
        current["granted_at"]
        and not future["granted_at"]
        and current["ends_at"] == future["starts_at"]
    )
    assert (
        db.execute(
            "SELECT COUNT(*) n FROM plan_credit_ledger WHERE kind='grant'"
        ).fetchone()["n"]
        == 1
    )


@pytest.mark.parametrize("method", ["Pix AI", "Dinheiro"])
def test_advance_payment_and_pickup_no_duplicate_charge(payments, method):
    app, client, db, _ = payments
    rules = policies.rules(db, "advance")
    for r in rules:
        r["priority"] = 1 if r["method"] == method else 2
    db.execute(
        "UPDATE customer_payment_policies SET rules=? WHERE scope='advance'",
        (json.dumps(rules),),
    )
    db.commit()
    pid = package(db)
    oid = advance(client, [pid]).json["id"]
    assert advance(client, [pid]).json["id"] == oid
    order = db.execute(
        "SELECT * FROM pickup_payment_orders WHERE id=?", (oid,)
    ).fetchone()
    assert order["local_method"] == method
    staff(client)
    path = "/painel/retirada/cobrancas/" + oid + "/confirmar-manual"
    for _ in range(2):
        assert (
            client.post(
                path, data={"payment_confirmed": "yes", "csrf_token": token(client)}
            ).status_code
            == 302
        )
    assert db.execute("SELECT COUNT(*) n FROM package_prepayments").fetchone()["n"] == 1
    response = deliver(client, [pid], method)
    assert response.status_code == 302
    withdrawal = db.execute("SELECT * FROM pickups").fetchone()
    assert withdrawal["total_paid"] == 0
    assert db.execute("SELECT COUNT(*) n FROM pickups").fetchone()["n"] == 1


def test_no_provider_routes_config_or_options(payments, monkeypatch):
    app, client, db, _ = payments
    assert not any(
        "fast" in rule.rule or "webhook" in rule.rule
        for rule in app.url_map.iter_rules()
    )
    for scope in policies.SCOPES:
        assert {r["method"] for r in policies.rules(db, scope)} == {
            "Pix AI",
            "Dinheiro",
        }
    assert not any(key.startswith("FASTFLOW_") for key in app.config)
    staff(client)
    for path in [
        "/painel",
        "/painel/configuracoes/plano",
        "/painel/configuracoes/pagamentos-cliente",
        "/painel/configuracoes/plano/clientes/1/validar",
    ]:
        page = client.get(path)
        assert page.status_code == 200
        assert "Pix Fast" not in page.text and "fastdepix.space" not in page.text
    assert not Path("fastflow_gateway.py").exists()


def test_purge_deletes_charges_but_preserves_entitlements_prepaid_and_receipts(
    payments,
):
    app, client, db, _ = payments
    now = datetime.now(TZ)
    old, _ = create_cycle(db, value=now)
    cycle_before = dict(db.execute("SELECT * FROM plan_cycles").fetchone())
    db.execute(
        "UPDATE plan_orders SET method='pix',local_method='Pix Fast',transaction_id='1001',paid_at=?,manual_paid_at=NULL WHERE id=?",
        (now.isoformat(), old["id"]),
    )
    db.commit()
    customer(client)
    pending_url = post_purchase(client).location
    pending_id = pending_url.rsplit("/", 1)[-1]
    db.execute(
        "UPDATE plan_orders SET method='pix',local_method='',transaction_id='127471' WHERE id=?",
        (pending_id,),
    )
    db.commit()
    pid = package(db)
    customer(client)
    oid = advance(client, [pid]).json["id"]
    staff(client)
    client.post(
        "/painel/retirada/cobrancas/" + oid + "/confirmar-manual",
        data={"payment_confirmed": "yes", "csrf_token": token(client)},
    )
    db.execute(
        "UPDATE pickup_payment_orders SET local_method='',callback_url='https://example.test/webhooks/fastflow/retiradas/old',transaction_id='9999' WHERE id=?",
        (oid,),
    )
    before_credit = [tuple(r) for r in db.execute("SELECT * FROM plan_credit_ledger")]
    db.execute("DELETE FROM system_migrations WHERE name='remove_legacy_provider_v1'")
    db.commit()
    from app import create_app

    restarted = create_app(app.config)
    other = restarted.test_client()
    customer(other)
    assert not db.execute(
        "SELECT 1 FROM plan_orders WHERE id IN (?,?)", (old["id"], pending_id)
    ).fetchone()
    assert not db.execute(
        "SELECT 1 FROM pickup_payment_orders WHERE id=?", (oid,)
    ).fetchone()
    cycle_after = dict(db.execute("SELECT * FROM plan_cycles").fetchone())
    assert {k: v for k, v in cycle_before.items() if k != "order_id"} == {
        k: v for k, v in cycle_after.items() if k != "order_id"
    }
    assert before_credit == [
        tuple(r) for r in db.execute("SELECT * FROM plan_credit_ledger")
    ]
    assert (
        db.execute("SELECT SUM(paid_cents) n FROM package_prepayments").fetchone()["n"]
        == 490
    )
    assert other.get(
        "/cliente/plano/pagamentos/" + old["id"] + "/comprovante.pdf"
    ).data.startswith(b"%PDF")
    if not db.postgres:
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    tables = (
        {
            r["table_name"]
            for r in db.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
        }
        if db.postgres
        else {
            r["name"]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    )

    assert (
        not {
            "plan_payment_events",
            "pickup_payment_events",
            "plan_api_budget",
            "plan_gateway_lock",
        }
        & tables
    )
    assert other.get("/cliente?aba=plano").status_code == 200
    create_app(app.config)
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1


def test_environment_removal_without_backup(tmp_path):
    env = tmp_path / "site.env"
    env.write_text(
        "DATABASE=unchanged\nFASTFLOW_API_KEY=private\nGMAIL_CLIENT_ID=keep\nFASTFLOW_WEBHOOK_SECRET=secret\nGOOGLE_LOGIN_ENABLED=1\n"
    )
    assert remove_environment(env) == 2
    assert (
        env.read_text()
        == "DATABASE=unchanged\nGMAIL_CLIENT_ID=keep\nGOOGLE_LOGIN_ENABLED=1\n"
    )
    assert list(tmp_path.iterdir()) == [env]


@pytest.mark.skipif(
    not __import__("os").environ.get("TEST_REMOVAL_DATABASE_URL"),
    reason="PostgreSQL de remoção não configurado",
)
def test_postgresql_provider_removal():
    import os
    from app import create_app
    from database import DatabaseConnection
    from test_app import register

    config = {
        "TESTING": True,
        "SECRET_KEY": "pg-removal",
        "DATABASE": os.environ["TEST_REMOVAL_DATABASE_URL"],
    }
    app = create_app(config)
    client = app.test_client()
    register(client)
    db = DatabaseConnection(config["DATABASE"])
    db.execute(
        "UPDATE clients SET confirmation_channel='email',confirmation_destination=email,confirmation_validated_at=accepted_at"
    )
    db.execute(
        "UPDATE settings SET pix_receiver_name='Fixture',pix_key_type='CNPJ',pix_key='38145273000105'"
    )
    db.commit()
    customer(client)
    try:
        test_purge_deletes_charges_but_preserves_entitlements_prepaid_and_receipts(
            (app, client, db, None)
        )
    finally:
        db.close()
