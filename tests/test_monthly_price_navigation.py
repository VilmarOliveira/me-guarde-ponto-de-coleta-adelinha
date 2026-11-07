"""Fictional prices/users, mocked API, immutable previously issued charges."""

import pytest
from datetime import datetime
from test_plan import site, post_purchase
from test_customer_payments import payments, staff, signed, admin_form
from test_advance_payments import customer
from test_customization import legacy_login
from test_app import token
import plan_service as ps
import plan_pricing as pricing
from core import TZ


def change(client, value):
    legacy_login(client)
    return client.post(
        "/painel/configuracoes/plano/mensalidade",
        data={"monthly_price": value, "csrf_token": token(client)},
        follow_redirects=True,
    )


def test_old_charge_retains_amount_new_renewal_uses_new_price(payments):
    app, client, db, fake = payments
    before = dict(
        db.execute("SELECT terms_text,signature,accepted_at FROM clients").fetchone()
    )
    post_purchase(client)
    old = dict(db.execute("SELECT * FROM plan_orders").fetchone())
    assert old["amount_cents"] == 3390
    assert "Mensalidade atualizada com sucesso." in change(client, "44,90").text
    assert pricing.current(db) == 4490
    customer(client)
    assert post_purchase(client).location.endswith(old["id"])
    assert (
        dict(
            db.execute("SELECT * FROM plan_orders WHERE id=?", (old["id"],)).fetchone()
        )
        == old
    )
    fake.records[old["transaction_id"]]["status"] = "paid"
    assert (
        signed(
            client, "/webhooks/fastflow/pedidos/" + old["id"], old["transaction_id"]
        ).status_code
        == 200
    )
    cycle = dict(db.execute("SELECT * FROM plan_cycles").fetchone())
    assert cycle["granted_at"]
    response = post_purchase(client)
    new = dict(
        db.execute("SELECT * FROM plan_orders WHERE id!=?", (old["id"],)).fetchone()
    )
    assert new["amount_cents"] == 4490 and "R$ 44,90" in new["rules_text"]
    assert new["rules_version"] != old["rules_version"]
    assert fake.records[new["transaction_id"]]["amount"] == 44.9
    fake.records[new["transaction_id"]]["status"] = "paid"
    fake.records[new["transaction_id"]]["amount"] = 33.90
    assert (
        signed(
            client, "/webhooks/fastflow/pedidos/" + new["id"], new["transaction_id"]
        ).status_code
        == 202
    )
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1
    fake.records[new["transaction_id"]]["amount"] = 44.90
    assert (
        signed(
            client, "/webhooks/fastflow/pedidos/" + new["id"], new["transaction_id"]
        ).status_code
        == 200
    )
    future = db.execute(
        "SELECT * FROM plan_cycles WHERE order_id=?", (new["id"],)
    ).fetchone()
    assert future["starts_at"] == cycle["ends_at"] and not future["granted_at"]
    assert (
        dict(
            db.execute(
                "SELECT terms_text,signature,accepted_at FROM clients"
            ).fetchone()
        )
        == before
    )
    assert "R$ 33,90" in client.get("/cliente/plano/pagamentos/" + old["id"]).text
    assert "R$ 44,90" in client.get(response.location).text
    assert (
        db.execute(
            "SELECT COUNT(*) n FROM audit_logs WHERE action=?", ("alterar_mensalidade",)
        ).fetchone()["n"]
        == 1
    )


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "1.234", "", "abc"])
def test_invalid_monthly_value_does_not_change_price(payments, value):
    _, client, db, fake = payments
    assert change(client, value).status_code == 200
    assert pricing.current(db) == 3390 and fake.creates == 0


def test_new_offer_public_comparison_and_stale_checkout(payments):
    _, client, db, fake = payments
    change(client, "39.90")
    customer(client)
    page = client.get("/cliente?aba=plano").text
    assert "R$ 39,90" in page and "R$ 3,99" in page and "R$ 7,98" in page
    assert "18,6%" in page and "19,4%" in page
    assert "R$ 39,90" in client.get("/").text
    response = client.post(
        "/cliente/plano/assinar",
        data={"plan_rules": "yes", "price_cents": "3390", "csrf_token": token(client)},
    )
    assert (
        response.status_code == 302
        and not db.execute("SELECT 1 FROM plan_orders").fetchone()
        and fake.creates == 0
    )
    response = client.post(
        "/cliente/plano/assinar",
        data={"plan_rules": "yes", "price_cents": "3990", "csrf_token": token(client)},
    )
    assert (
        response.status_code == 302
        and db.execute("SELECT amount_cents FROM plan_orders").fetchone()[
            "amount_cents"
        ]
        == 3990
    )


def test_manual_old_order_payment_and_new_administrative_amount(payments):
    _, client, db, fake = payments
    # Existing manual order is still paid at its captured amount.
    from test_payment_policies_history import configure

    configure(db, "subscription", **{"Pix Fast": {"enabled": False}})
    post_purchase(client)
    order = db.execute("SELECT * FROM plan_orders").fetchone()
    change(client, "49.90")
    path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/approve"
    assert client.post(path, data=admin_form(client, path, "Pix AI")).status_code == 302
    record = db.execute(
        "SELECT * FROM plan_admin_decisions WHERE action=?", ("approve",)
    ).fetchone()
    assert record["amount_cents"] == 3390
    path = "/painel/configuracoes/plano/clientes/1/validar"
    assert (
        client.post(path, data=admin_form(client, path, "Dinheiro")).status_code == 302
    )
    nextorder = db.execute(
        "SELECT * FROM plan_orders WHERE id!=?", (order["id"],)
    ).fetchone()
    assert nextorder["amount_cents"] == 4990 and fake.creates == 0


def test_navigation_separates_public_and_internal_and_permissions(payments):
    app, client, db, fake = payments
    customer(client)
    internal = (
        client.get("/cliente")
        .text.split('<nav id="main-nav"', 1)[1]
        .split("</nav>", 1)[0]
    )
    assert (
        "Dúvidas" not in internal
        and "> Blog" not in internal
        and "Cadastre-se" not in internal
    )
    assert "Sair" in internal
    assert client.get("/painel").status_code == 302
    assert (
        client.post(
            "/painel/configuracoes/plano/mensalidade",
            data={"monthly_price": "1", "csrf_token": token(client)},
        ).status_code
        == 302
    )
    sid = db.execute(
        "INSERT INTO staff_users(name,email,password_hash,role,can_settings,can_packages,can_pickups,created_at) VALUES('Atendente fixture','price-staff@example.test','fixture','atendente',0,1,1,?) RETURNING id",
        (datetime.now(TZ).isoformat(),),
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True, staff_user_id=sid, staff_session_version=1)
    panel = (
        client.get("/painel")
        .text.split('<nav id="main-nav"', 1)[1]
        .split("</nav>", 1)[0]
    )
    assert "Dúvidas" not in panel and "Gerenciar blog" not in panel and "Sair" in panel
    assert client.get("/painel/blog").status_code == 403
    assert (
        client.post(
            "/painel/configuracoes/plano/mensalidade",
            data={"monthly_price": "1", "csrf_token": token(client)},
        ).status_code
        == 403
    )
    legacy_login(client)
    panel = (
        client.get("/painel")
        .text.split('<nav id="main-nav"', 1)[1]
        .split("</nav>", 1)[0]
    )
    assert "Gerenciar blog" in panel and "Dúvidas" not in panel
    guest = app.test_client()
    public = guest.get("/").text
    assert (
        "Dúvidas" in public
        and "Cadastre-se" in public
        and 'href="/cliente/login"' in public
        and 'href="/login"' in public
    )
    assert pricing.current(db) == 3390


@pytest.mark.skipif(
    not __import__("os").environ.get("TEST_PRICE_DATABASE_URL"),
    reason="PostgreSQL isolado para preço não configurado",
)
def test_postgres_migration_preserves_price_and_old_payment(payments):
    import os
    from app import create_app
    from database import DatabaseConnection
    from scripts.migrate_sqlite_to_postgres import migrate

    app, client, db, fake = payments
    post_purchase(client)
    old = dict(db.execute("SELECT * FROM plan_orders").fetchone())
    change(client, "39.90")
    target = os.environ["TEST_PRICE_DATABASE_URL"]
    migrate(app.config["DATABASE"], target)
    migrated = create_app({**app.config, "DATABASE": target})
    native = migrated.test_client()
    targetdb = DatabaseConnection(target)
    assert pricing.current(targetdb) == 3990
    assert (
        targetdb.execute(
            "SELECT amount_cents FROM plan_orders WHERE id=?", (old["id"],)
        ).fetchone()["amount_cents"]
        == 3390
    )
    fake.records[old["transaction_id"]]["status"] = "paid"
    assert (
        signed(
            native, "/webhooks/fastflow/pedidos/" + old["id"], old["transaction_id"]
        ).status_code
        == 200
    )
    customer(native)
    post_purchase(native)
    new = targetdb.execute(
        "SELECT * FROM plan_orders WHERE id!=?", (old["id"],)
    ).fetchone()
    assert (
        new["amount_cents"] == 3990
        and fake.records[new["transaction_id"]]["amount"] == 39.9
    )
    assert targetdb.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1
    targetdb.close()


@pytest.mark.parametrize("receive,withdraw", [(1, 0), (0, 1)])
def test_operation_actions_match_profile_and_direct_permissions(
    payments, receive, withdraw
):
    _, client, db, _ = payments
    sid = db.execute(
        "INSERT INTO staff_users(name,email,password_hash,role,can_clients,can_packages,can_pickups,created_at) VALUES(?,?,?,?,1,?,?,?) RETURNING id",
        (
            "Perfil ficticio",
            "scope-%s@example.test" % receive,
            "fixture",
            "atendente",
            receive,
            withdraw,
            datetime.now(TZ).isoformat(),
        ),
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True, staff_user_id=sid, staff_session_version=1)
    page = client.get("/painel").text
    assert ('id="package-form"' in page) == bool(receive)
    assert ('id="pickup-form"' in page) == bool(withdraw)
    if not receive:
        assert (
            client.post("/pacotes", data={"csrf_token": token(client)}).status_code
            == 403
        )
    if not withdraw:
        assert (
            client.post("/retiradas", data={"csrf_token": token(client)}).status_code
            == 403
        )
