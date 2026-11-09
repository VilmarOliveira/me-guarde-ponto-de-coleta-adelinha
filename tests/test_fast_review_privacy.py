"""Read-only reset preparation and customer-only notices. All provider calls simulated."""

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest
from test_plan import site, CONFIG, post_purchase, create_cycle
from test_customer_payments import payments, package, signed
from test_advance_payments import advance, customer
from test_app import token
from core import TZ
from fastflow_gateway import FastFlow, Settings, GatewayError
from scripts.review_fast_integration import inventory
import payment_policies as policies


@pytest.mark.parametrize("authenticated", [False, True])
def test_public_pages_never_include_customer_pending_card(payments, authenticated):
    app, client, _, _ = payments
    if not authenticated:
        client = app.test_client()
    else:
        customer(client)
    for path in (
        "/",
        "/cadastro",
        "/cliente/login",
        "/cliente/recuperar-senha",
        "/privacidade",
        "/condicoes",
        "/blog",
        "/duvidas-frequentes",
        "/login",
    ):
        page = client.get(path)
        assert page.status_code == 200
        assert 'href="/cliente/pendencias-e-autorizacoes"' not in page.text, path
    if authenticated:
        assert "Pendências e autorizações" in client.get("/cliente/editar").text
        client.post("/cliente/sair", data={"csrf_token": token(client)})
        assert 'href="/cliente/pendencias-e-autorizacoes"' not in client.get("/").text
        assert client.get("/cliente/editar").status_code == 302


def test_review_preserves_uncertain_and_financial_records(payments):
    app, client, db, fake = payments
    post_purchase(client)
    oid = db.execute("SELECT id FROM plan_orders").fetchone()["id"]
    db.execute(
        "UPDATE plan_orders SET transaction_id='127471',error_code='provider_forbidden' WHERE id=?",
        (oid,),
    )
    db.execute(
        "INSERT INTO plan_payment_events(event_key,event_type,received_at,outcome) VALUES('detached-test','integration.test','2026-01-01','processed')"
    )
    db.execute(
        "INSERT INTO plan_payment_events(order_id,transaction_id,event_key,event_type,received_at,outcome) VALUES(?,'127471','linked-test','integration.test','2026-01-01','processed')",
        (oid,),
    )
    db.execute(
        "INSERT INTO plan_payment_events(event_key,event_type,received_at,outcome) VALUES('unknown-record','unknown','2026-01-01','processed')"
    )
    db.commit()
    before = [tuple(r) for r in db.execute("SELECT * FROM plan_orders").fetchall()]
    events = [
        tuple(r) for r in db.execute("SELECT * FROM plan_payment_events").fetchall()
    ]
    report = inventory(db, app.config)
    assert report["mode"] == "review_only_no_deletions" and report["requires_approval"]
    row = report["charges_for_review"][0]
    assert (
        row["transaction_id"] == "127471"
        and row["local_state"] == "pending"
        and row["action"] == "preserve_and_reconcile_manually"
    )
    candidates = report["proposed_cleanup"]["detached_explicit_test_event_ids"]
    assert len(candidates) == 1
    assert before == [
        tuple(r) for r in db.execute("SELECT * FROM plan_orders").fetchall()
    ]
    assert events == [
        tuple(r) for r in db.execute("SELECT * FROM plan_payment_events").fetchall()
    ]
    rendered = json.dumps(report)
    assert (
        CONFIG["FASTFLOW_API_KEY"] not in rendered
        and CONFIG["FASTFLOW_WEBHOOK_SECRET"] not in rendered
    )
    assert (
        "bank_qr_acceptance" in report
        and report["bank_qr_acceptance"] == "not_verified"
    )


def test_cli_backup_and_review_are_private_and_non_destructive(payments, tmp_path):
    app, client, db, _ = payments
    post_purchase(client)
    output = tmp_path / "review.json"
    backups = tmp_path / "private-backups"
    env = {**os.environ, "DATABASE_URL": "", "DATABASE": app.config["DATABASE"]}
    script = Path(__file__).resolve().parents[1] / "scripts/review_fast_integration.py"
    run = subprocess.run(
        [
            sys.executable,
            str(script),
            "--output",
            str(output),
            "--backup-dir",
            str(backups),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    report = json.loads(output.read_text())
    backup = Path(report["backup"]["path"])
    assert output.stat().st_mode & 0o777 == 0o600
    assert backup.stat().st_mode & 0o777 == 0o600
    saved = sqlite3.connect(backup)
    assert saved.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert saved.execute("SELECT COUNT(*) FROM plan_orders").fetchone()[0] == 1
    saved.close()
    assert db.execute("SELECT COUNT(*) n FROM plan_orders").fetchone()["n"] == 1
    assert (
        subprocess.run(
            [sys.executable, str(script), "--apply"], env=env, capture_output=True
        ).returncode
        != 0
    )


@pytest.mark.parametrize("flow", ["subscription", "renewal", "advance"])
@pytest.mark.parametrize("status", ["paid", "expired"])
def test_existing_fast_flows_confirm_states_once_with_new_credentials_configuration(
    payments, flow, status
):
    app, client, db, fake = payments
    if flow == "renewal":
        create_cycle(db, fake, datetime.now(TZ) - timedelta(days=5))
        fake.state = "pending"
    if flow == "advance":
        oid = advance(client, [package(db)]).json["id"]
        table = "pickup_payment_orders"
        route = "retiradas"
    else:
        oid = post_purchase(client).location.rsplit("/", 1)[-1]
        table = "plan_orders"
        route = "pedidos"
    order = dict(
        db.execute("SELECT * FROM " + table + " WHERE id=?", (oid,)).fetchone()
    )
    assert order["qr_text"] == fake.records[order["transaction_id"]]["qr_code_text"]
    fake.records[order["transaction_id"]]["status"] = status
    callback = "/webhooks/fastflow/" + route + "/" + oid
    signed(client, callback, order["transaction_id"])
    signed(client, callback, order["transaction_id"])
    current = db.execute("SELECT * FROM " + table + " WHERE id=?", (oid,)).fetchone()
    assert current["state"] == status
    if status == "paid":
        if flow == "advance":
            assert (
                db.execute("SELECT COUNT(*) n FROM package_prepayments").fetchone()["n"]
                == 1
            )
        else:
            assert (
                db.execute(
                    "SELECT COUNT(*) n FROM plan_cycles WHERE order_id=?", (oid,)
                ).fetchone()["n"]
                == 1
            )
    else:
        path = (
            "/cliente/pacotes/pagamentos/"
            if flow == "advance"
            else "/cliente/plano/pagamentos/"
        )
        assert "data-plan-pix" not in client.get(path + oid).text


def test_pause_new_fast_keeps_webhooks_existing_charges_and_ai_cash(
    payments, monkeypatch
):
    app, client, db, fake = payments
    post_purchase(client)
    row = db.execute("SELECT * FROM plan_orders").fetchone()
    app.config["FASTFLOW_NEW_CHARGES_ENABLED"] = "0"
    settings = Settings.from_config(app.config)
    assert settings.ready and not settings.new_charges_enabled
    assert policies.select(db, app, "advance", 490)["method"] == "Pix AI"
    rules = policies.rules(db, "advance")
    for rule in rules:
        if rule["method"] == "Pix AI":
            rule["enabled"] = False
    assert (
        policies.select(db, app, "advance", 490, configured=rules)["method"]
        == "Dinheiro"
    )
    monkeypatch.setattr(
        "fastflow_gateway.requests.request",
        lambda *a, **k: pytest.fail("Must not contact the provider to create a charge"),
    )
    with pytest.raises(GatewayError, match="pausadas"):
        FastFlow(settings).create(dict(row), {})
    client.post(
        "/cliente/plano/pagamentos/" + row["id"] + "/novo-pix",
        data={"replace_pending": "yes", "csrf_token": token(client)},
    )
    assert fake.creates == 1
    fake.records[row["transaction_id"]]["status"] = "paid"
    signed(client, "/webhooks/fastflow/pedidos/" + row["id"], row["transaction_id"])
    assert db.execute("SELECT state FROM plan_orders").fetchone()["state"] == "paid"


def test_postgres_backup_does_not_put_password_in_command(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from scripts.review_fast_integration import backup_database

    captured = {}

    def run(command, **kwargs):
        captured.update(command=command, env=kwargs["env"])
        Path(command[command.index("--file") + 1]).write_bytes(b"fixture-backup")

    monkeypatch.setattr("scripts.review_fast_integration.subprocess.run", run)
    result = backup_database(
        SimpleNamespace(postgres=True),
        "postgresql://fixture:private-fixture-password@localhost/test",
        tmp_path,
    )
    assert "private-fixture-password" not in " ".join(captured["command"])
    assert captured["env"]["PGPASSWORD"] == "private-fixture-password"
    assert result["bytes"] > 0 and result["restore_tested"] is False


@pytest.mark.parametrize("flow", ["subscription", "renewal", "advance"])
@pytest.mark.parametrize("manual", ["Pix AI", "Dinheiro"])
def test_new_charge_pause_preserves_manual_flows(payments, flow, manual):
    app, client, db, fake = payments
    if flow == "renewal":
        create_cycle(db, fake, datetime.now(TZ) - timedelta(days=5))
        fake.state = "pending"
    app.config["FASTFLOW_NEW_CHARGES_ENABLED"] = "0"
    if manual == "Dinheiro":
        rules = policies.rules(db, flow)
        for rule in rules:
            if rule["method"] == "Pix AI":
                rule["enabled"] = False
        db.execute(
            "UPDATE customer_payment_policies SET rules=? WHERE scope=?",
            (json.dumps(rules), flow),
        )
        db.commit()
    before = fake.creates
    if flow == "advance":
        oid = advance(client, [package(db)]).json["id"]
        table = "pickup_payment_orders"
    else:
        oid = post_purchase(client).location.rsplit("/", 1)[-1]
        table = "plan_orders"
    order = db.execute("SELECT * FROM " + table + " WHERE id=?", (oid,)).fetchone()
    assert order["local_method"] == manual and order["state"] == "pending"
    assert fake.creates == before
