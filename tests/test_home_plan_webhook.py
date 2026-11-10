"""Signed provider health checks and the public Pix-only plan offer."""

import hashlib
import hmac
import json
import pytest
from test_plan import site, CONFIG, post_purchase, create_cycle, START
from test_app import token
import plan_service as ps
from fastflow_gateway import Settings

PANEL_TEST = {
    "data": {
        "note": "Evento de teste disparado pelo painel do parceiro",
        "reference": "REFERENCIA_DO_TESTE",
    },
    "test": True,
    "event": "transaction.created",
    "timestamp": "2026-11-03T01:07:42-03:00",
}
TABLES = (
    "clients",
    "packages",
    "plan_orders",
    "plan_cycles",
    "plan_credit_ledger",
    "plan_payment_events",
    "plan_accounts",
)


def snapshot(db):
    return {
        t: [tuple(row) for row in db.execute("SELECT * FROM " + t).fetchall()]
        for t in TABLES
    }


def callback(
    client,
    payload=None,
    raw=None,
    secret=CONFIG["FASTFLOW_WEBHOOK_SECRET"],
    path="/webhooks/fastflow",
):
    raw = json.dumps(payload).encode() if raw is None else raw
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        path,
        data=raw,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": "sha256=" + signature,
        },
    )


@pytest.mark.parametrize(
    "path", ["/webhooks/fastflow", "/webhooks/fastflow/pedidos/nenhum-pedido"]
)
def test_signed_panel_test_no_api_or_financial_change(site, monkeypatch, path):
    _, client, db, _ = site
    before = snapshot(db)

    def forbidden(*a, **k):
        raise AssertionError("Panel health checks must never access FastFlow")

    monkeypatch.setattr("plan_routes.gateway", forbidden)
    for _ in range(2):
        assert callback(client, PANEL_TEST, path=path).status_code == 200
    assert snapshot(db) == before


@pytest.mark.parametrize(
    "payload",
    [
        PANEL_TEST,
        {
            **PANEL_TEST,
            "data": {"transaction_id": "123", "status": "paid", "amount": 33.9},
        },
    ],
)
def test_panel_test_invalid_signature_rejected(site, payload):
    _, client, db, fake = site
    before = snapshot(db)
    assert callback(client, payload, secret="invalid").status_code == 401
    assert fake.gets == 0 and fake.creates == 0 and snapshot(db) == before


@pytest.mark.parametrize("raw", [b"{invalid", b"[]", b"null", b'"test"', b"false"])
def test_signed_invalid_json_or_non_object_rejected(site, raw):
    _, client, db, fake = site
    before = snapshot(db)
    assert callback(client, raw=raw).status_code == 400
    assert fake.gets == 0 and snapshot(db) == before


@pytest.mark.parametrize("flag", ["true", 1, False, None])
def test_health_check_requires_boolean_true(site, flag):
    _, client, _, _ = site
    assert callback(client, {**PANEL_TEST, "test": flag}).status_code == 400


def test_panel_test_configuration_and_body_limit_still_enforced(site):
    app, client, _, _ = site
    assert callback(client, raw=b"x" * 65537).status_code == 413
    app.config["FASTFLOW_ENABLED"] = "0"
    assert callback(client, PANEL_TEST).status_code == 503


def test_real_webhook_still_queries_api_and_deduplicates(site):
    _, client, db, fake = site
    order, data = create_cycle(db, fake, state="pending")
    fake.records[data["id"]]["status"] = "paid"
    event = {"data": {"transaction_id": data["id"], "status": "paid"}, "test": False}
    path = "/webhooks/fastflow/pedidos/" + order["id"]
    assert callback(client, event, path=path).status_code == 200
    calls = fake.gets
    assert calls > 0
    assert callback(client, event, path=path).status_code == 200 and fake.gets == calls
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1
    assert db.execute("SELECT COUNT(*) n FROM plan_credit_ledger").fetchone()["n"] == 1


def test_home_offer_server_prices_and_required_content(site):
    _, client, _, _ = site
    page = client.get("/")
    assert page.status_code == 200
    for text in (
        "Como prefere pagar?",
        "PLANO MENSAL",
        "Vantagens do plano",
        "R$ 33,90",
        "ECONOMIZE ATÉ 31%",
        "10 pacotes pequenos ou 5 grandes",
        "Cada grande equivale a 2 pequenos.",
        "Recebimento já pago",
        "Renove por Pix, sem débito automático.",
        "Créditos não acumulam. Taxas de atraso à parte.",
        "POR PACOTE",
        "R$ 4,90",
        "R$ 9,90",
        "altura até 80 cm e peso até 10 kg",
        "altura até 150 cm e peso até 20 kg",
        "Tanto a soma das medidas quanto o peso",
        "Pagamento avulso na retirada.",
    ):
        assert text in page.text
    assert page.text.count('id="planos-e-precos"') == 1
    assert "Preços por pacote" not in page.text and "SIMULAÇÃO" not in page.text
    assert "Economia em relação ao pacote avulso" not in page.text
    assert 'href="/plano/assinar"' in page.text


def test_offer_reads_billing_constants(monkeypatch):
    monkeypatch.setattr("core.SMALL_PACKAGE_PRICE", 5.2)
    monkeypatch.setattr("core.SMALL_PACKAGE_MAX_SUM", 81)
    data = ps.public_offer()
    assert (
        data["small_price"] == 5.2
        and data["small_sum"] == 81
        and data["monthly_price"] == 33.9
    )


def test_subscribe_login_returns_to_plan_password_and_failed_retry(site):
    _, client, _, fake = site
    with client.session_transaction() as session:
        session.clear()
    response = client.get("/plano/assinar")
    assert response.location.endswith("/cliente/login")
    client.post(
        "/cliente/login",
        data={
            "cpf": "cliente@example.com",
            "password": "incorrect",
            "csrf_token": token(client),
        },
    )
    response = client.post(
        "/cliente/login",
        data={
            "cpf": "cliente@example.com",
            "password": "senha-segura",
            "csrf_token": token(client),
        },
    )
    assert response.location.endswith("/cliente/plano") and fake.creates == 0
    assert client.get(response.location).status_code == 200
    with client.session_transaction() as session:
        assert "client_login_destination" not in session


def test_regular_login_destination_is_unchanged_and_not_open_redirect(site):
    _, client, _, _ = site
    with client.session_transaction() as session:
        session.clear()
    client.get("/cliente/login?next=https://evil.invalid")
    response = client.post(
        "/cliente/login",
        data={
            "cpf": "cliente@example.com",
            "password": "senha-segura",
            "csrf_token": token(client),
        },
    )
    assert response.location.endswith("/cliente")


def test_active_plan_button_consults_current_cycle_without_charge(site):
    _, client, db, fake = site
    create_cycle(db, fake)
    count = fake.creates
    assert client.get("/plano/assinar").location.endswith("/cliente/plano")
    assert fake.creates == count
    with client.session_transaction() as session:
        session.clear()
        session.update(staff=True)
    assert client.get("/plano/assinar").location.endswith("/painel")
    with client.session_transaction() as session:
        assert session.get("staff")


def test_card_disabled_even_if_old_environment_enabled_it(site):
    app, client, db, fake = site
    app.config.update(FASTFLOW_CARD_ENABLED="1", FASTFLOW_CARD_PCI_READY="1")
    assert not Settings.from_config(app.config).card_enabled
    page = client.get("/cliente/plano")
    assert (
        'option value="card"' not in page.text and 'name="card_number"' not in page.text
    )
    assert (
        post_purchase(
            client, method="card", card_number="4111111111111111", card_cvv="123"
        ).status_code
        == 302
    )
    assert fake.creates == 0 and not db.execute("SELECT 1 FROM plan_orders").fetchone()
