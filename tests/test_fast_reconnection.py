import json

from app import create_app
from fastflow_gateway import Settings
import payment_policies

from test_app import login
from test_customer_payments import payments
from test_plan import post_purchase, site


def test_fast_reconnection_requires_fresh_environment_names(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTFLOW_ENABLED", "1")
    monkeypatch.setenv("FASTFLOW_API_KEY", "legacy-ignored")
    monkeypatch.setenv("FASTFLOW_WEBHOOK_SECRET", "x" * 32)
    monkeypatch.setenv("FASTFLOW_PUBLIC_ORIGIN", "https://legacy.example.test")
    config = {"TESTING": True, "DATABASE": str(tmp_path / "reconnect.db")}
    old = Settings.from_config(create_app(config).config)
    assert not old.ready and not old.api_key and not old.new_charges_enabled

    monkeypatch.setenv("FAST_PIX_ENABLED", "1")
    monkeypatch.setenv("FAST_PIX_API_KEY", "new-fixture-key")
    monkeypatch.setenv("FAST_PIX_WEBHOOK_SECRET", "y" * 32)
    monkeypatch.setenv("FAST_PIX_PUBLIC_ORIGIN", "https://new.example.test")
    new = Settings.from_config(create_app(config).config)
    assert new.ready and not new.new_charges_enabled and not new.card_enabled
    assert new.callback("new-order").startswith("https://new.example.test/")


def test_reconnection_preserves_manual_preferences_and_receipt(payments):
    app, client, db, _ = payments
    manual = [rule for rule in payment_policies.defaults() if rule["method"] != "Pix Fast"]
    for position, rule in enumerate(manual, 1):
        rule["priority"] = position
    db.execute(
        "UPDATE customer_payment_policies SET rules=? WHERE scope='subscription'",
        (json.dumps(manual),),
    )
    db.execute(
        "INSERT INTO preserved_plan_documents(id,client_id,document,created_at) VALUES(?,1,?,?)",
        ("old-receipt", b"%PDF-fixture", "2026-11-08T12:00:00-03:00"),
    )
    db.commit()
    rules = payment_policies.rules(db, "subscription")
    assert [(r["method"], r["priority"]) for r in rules] == [
        ("Pix AI", 1), ("Dinheiro", 2), ("Pix Fast", 3)
    ]
    assert payment_policies.select(db, app, "subscription", 3390)["method"] == "Pix AI"
    path = "/cliente/plano/pagamentos/old-receipt/comprovante.pdf"
    own = client.get(path)
    assert own.status_code == 200 and own.data == b"%PDF-fixture"
    assert "no-store" in own.headers["Cache-Control"]
    assert "Baixar comprovante anterior" in client.get("/cliente?aba=plano").text
    guest = app.test_client()
    assert guest.get(path).status_code == 302
    login(guest)
    assert guest.get("/painel/configuracoes/plano/pagamentos/old-receipt/comprovante.pdf").data == own.data


def test_old_transactions_do_not_activate_if_removal_was_skipped(payments):
    app, client, db, fake = payments
    post_purchase(client)
    order = dict(db.execute("SELECT * FROM plan_orders").fetchone())
    db.execute("DELETE FROM system_migrations WHERE name='fresh_pix_fast_v1'")
    db.commit()
    restarted = create_app(dict(app.config))
    assert not Settings.from_config(restarted.config).ready
    assert fake.creates == 1
    assert dict(db.execute("SELECT * FROM plan_orders").fetchone()) == order
