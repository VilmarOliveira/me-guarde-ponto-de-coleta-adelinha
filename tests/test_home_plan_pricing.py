"""Signed provider health checks and the public Pix-only plan offer."""

import json
import pytest
from test_plan import site, CONFIG, post_purchase, create_cycle, START
from test_app import token
import plan_service as ps
from payment_domain import LocalSettings as Settings



















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
