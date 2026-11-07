"""Requested navigation/registration and financial archival; all payments mocked."""

import re
import pytest
from test_plan import site, post_purchase
from test_customer_payments import (
    payments,
    package,
    staff,
    signed,
    create_pickup,
    admin_form,
)
from test_advance_payments import customer, advance
from test_customization import legacy_login
from test_app import token
from fastflow_gateway import GatewayError, FastFlow, Settings
from test_plan import CONFIG


def test_home_registration_map_and_six_customer_tabs(payments):
    app, client, db, _ = payments
    home = app.test_client().get("/").text
    assert 'id="registration-form"' not in home
    assert "FAZER CADASTRO AGORA" in home and 'href="/cadastro"' in home
    assert "Conheça o serviço" not in home and ">Preços e planos</a>" not in home
    assert "https://maps.app.goo.gl/5rCTnEns2EDe9RhV6" in home
    assert "1. Cadastre-se no nosso site" in home
    assert (
        "Antes de fazer sua compra, crie sua conta no site do Ponto de Coleta Adelinha."
        in home
    )
    registration = client.get("/cadastro").text
    assert (
        'id="registration-form"' in registration and "Voltar ao início" in registration
    )
    assert (
        "residential_street" in registration
        and "confirmation_code" in registration
        and 'name="signature"' in registration
    )
    assert "residencial é separado" in registration
    customer(client)
    area = client.get("/cliente").text
    nav = area.split('class="customer-tabs"', 1)[1].split("</nav>", 1)[0]
    labels = [
        "Visão geral",
        "Meus pacotes",
        "Meu plano",
        "Histórico",
        "Meus dados",
        "Contratos e autorizações",
    ]
    assert [nav.index(label) for label in labels] == sorted(
        nav.index(label) for label in labels
    )
    assert '<details class="customer-area-menu"' not in area


def test_staff_menu_and_settings_task_cards(payments):
    _, client, db, _ = payments
    legacy_login(client)
    page = client.get("/painel").text
    nav = page.split('<nav id="main-nav"', 1)[1].split("</nav>", 1)[0]
    assert (
        nav.index("Painel de métricas") < nav.index("Configurações") < nav.index("Sair")
    )
    assert (
        "Pacotes e retiradas" not in nav
        and "Gerenciar blog" not in nav
        and "> Histórico" not in nav
    )
    hub = client.get("/painel/configuracoes").text
    assert (
        "Pacotes e retiradas" in hub
        and "Gerenciar blog" in hub
        and "Histórico</b>" in hub
    )
    assert "Plano ME GUARDE+</b>" in hub and "Plano ME GUARDE Mais</b>" not in hub
    sid = db.execute(
        "INSERT INTO staff_users(name,email,password_hash,role,can_settings,can_users,can_packages,can_pickups,created_at) VALUES('Operador ficticio','eleven-crew@example.test','fixture','atendente',0,0,1,1,'2026-11-07T10:00:00-03:00') RETURNING id"
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True, staff_user_id=sid, staff_session_version=1)
    assert client.get("/painel/configuracoes").status_code == 200
    hub = client.get("/painel/configuracoes").text
    assert (
        "Pacotes e retiradas" in hub
        and "Histórico</b>" in hub
        and "Gerenciar blog" not in hub
    )
    assert client.get("/painel/blog").status_code == 403


def test_visible_provider_alias_preserves_internal_callback(payments):
    _, client, db, fake = payments
    post_purchase(client)
    order = db.execute("SELECT * FROM plan_orders").fetchone()
    db.execute(
        "UPDATE plan_orders SET policy_snapshot=? WHERE id=?",
        ('{"title":"FastFlow","text":"Confirmação na FastFlow."}', order["id"]),
    )
    db.commit()
    page = client.get("/cliente/plano/pagamentos/" + order["id"]).text
    assert "FastFlow" not in page and "Confirmação na Fast." in page
    assert "/webhooks/fastflow/pedidos/" in order["callback_url"]
    assert fake.records[order["transaction_id"]]["payment_provider"] == "fastflow"


def test_documented_cancel_is_delete(monkeypatch):
    calls = []
    flow = FastFlow(Settings.from_config(CONFIG))
    monkeypatch.setattr(
        flow, "request", lambda *args, **kwargs: calls.append((args, kwargs))
    )
    flow.cancel("12")
    assert calls == [(("DELETE", "/transactions/12"), {})]


@pytest.mark.parametrize("failure", [False, True])
def test_pickup_cancel_confirmed_or_real_status_retained(
    payments, monkeypatch, failure
):
    _, client, db, fake = payments
    pid = package(db)
    staff(client)
    oid = create_pickup(client, [pid]).json["id"]
    before = dict(db.execute("SELECT * FROM pickup_payment_orders").fetchone())
    if failure:

        def fail(*args):
            raise GatewayError("Fast indisponível.", code="unavailable")

        monkeypatch.setattr(fake, "cancel", fail)
    response = client.post(
        "/painel/retirada/pix-fast/" + oid + "/cancelar",
        data={"csrf_token": token(client)},
        follow_redirects=True,
    )
    row = db.execute("SELECT * FROM pickup_payment_orders").fetchone()
    assert row["state"] == ("pending" if failure else "cancelled")
    assert (
        ("não foi possível" in response.text.lower())
        if failure
        else ("Cancelada" in response.text)
    )
    if failure:
        assert (
            row["transaction_id"] == before["transaction_id"]
            and row["qr_text"] == before["qr_text"]
        )
    assert not db.execute("SELECT 1 FROM pickups").fetchone()


def test_paid_charge_cannot_be_cancelled(payments, monkeypatch):
    _, client, db, fake = payments
    pid = package(db)
    staff(client)
    oid = create_pickup(client, [pid]).json["id"]
    row = db.execute("SELECT * FROM pickup_payment_orders").fetchone()
    fake.records[row["transaction_id"]]["status"] = "paid"
    signed(client, "/webhooks/fastflow/retiradas/" + oid, row["transaction_id"])
    monkeypatch.setattr(
        fake, "cancel", lambda *args: pytest.fail("Must not cancel paid")
    )
    client.post(
        "/painel/retirada/pix-fast/" + oid + "/cancelar",
        data={"csrf_token": token(client)},
    )
    assert (
        db.execute("SELECT state FROM pickup_payment_orders").fetchone()["state"]
        == "paid"
    )


def test_archive_pickup_hides_qr_keeps_hold_and_flags_late_payment(payments):
    _, client, db, fake = payments
    pid = package(db)
    oid = advance(client, [pid]).json["id"]
    row = dict(db.execute("SELECT * FROM pickup_payment_orders").fetchone())
    staff(client)
    path = "/painel/retirada/pix-fast/" + oid + "/arquivar"
    assert client.post(path, data={"csrf_token": token(client)}).status_code == 302
    archived = db.execute("SELECT * FROM pickup_payment_orders").fetchone()
    assert (
        archived["archived_at"]
        and archived["state"] == "pending"
        and archived["qr_text"] == row["qr_text"]
    )
    assert oid not in client.get("/painel/retirada/cobrancas").text
    assert oid in client.get("/painel/retirada/cobrancas?arquivadas=1").text
    customer(client)
    page = client.get("/cliente/pacotes/pagamentos/" + oid).text
    assert 'id="advance-code"' not in page and "Cobrança arquivada" in page
    assert advance(client, [pid]).json["id"] == oid and fake.creates == 1
    fake.records[row["transaction_id"]]["status"] = "paid"
    signed(client, "/webhooks/fastflow/retiradas/" + oid, row["transaction_id"])
    after = db.execute("SELECT * FROM pickup_payment_orders").fetchone()
    assert (
        after["state"] == "paid"
        and after["paid_at"]
        and after["error_code"] == "archived_payment_review"
    )
    assert not db.execute("SELECT 1 FROM package_prepayments").fetchone()
    assert not db.execute("SELECT 1 FROM pickups").fetchone()
    assert advance(client, [pid]).json["id"] == oid and fake.creates == 1


def test_archive_plan_preserves_status_blocks_new_and_never_activates_late_payment(
    payments,
):
    _, client, db, fake = payments
    post_purchase(client)
    order = dict(db.execute("SELECT * FROM plan_orders").fetchone())
    legacy_login(client)
    assert (
        client.post(
            "/painel/configuracoes/plano/pagamentos/" + order["id"] + "/arquivar",
            data={"csrf_token": token(client)},
        ).status_code
        == 302
    )
    assert order["id"] not in client.get("/painel/configuracoes/plano").text
    assert order["id"] in client.get("/painel/configuracoes/plano?arquivadas=1").text
    customer(client)
    assert (
        'id="plan-pix"'
        not in client.get("/cliente/plano/pagamentos/" + order["id"]).text
    )
    assert post_purchase(client).location.endswith(order["id"]) and fake.creates == 1
    fake.records[order["transaction_id"]]["status"] = "paid"
    signed(client, "/webhooks/fastflow/pedidos/" + order["id"], order["transaction_id"])
    current = db.execute("SELECT * FROM plan_orders").fetchone()
    assert (
        current["state"] == "paid"
        and current["paid_at"]
        and current["error_code"] == "archived_payment_review"
    )
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone()
    assert post_purchase(client).location.endswith(order["id"]) and fake.creates == 1


def test_delete_empty_response_still_requires_provider_lookup(monkeypatch):
    from types import SimpleNamespace
    import fastflow_gateway as module

    flow = FastFlow(Settings.from_config(CONFIG))
    calls = []
    monkeypatch.setattr(
        module.requests,
        "request",
        lambda *a, **kw: (calls.append((a, kw)) or SimpleNamespace(status_code=204)),
    )
    assert flow.cancel("12") == {}
    assert calls[0][0][0] == "DELETE"


def test_archived_charge_reconciles_without_webhook_and_persists(payments):
    from datetime import datetime, timedelta
    from core import TZ
    import pickup_payments
    from app import create_app

    _, client, db, fake = payments
    pid = package(db)
    oid = advance(client, [pid]).json["id"]
    row = dict(db.execute("SELECT * FROM pickup_payment_orders").fetchone())
    staff(client)
    client.post(
        "/painel/retirada/pix-fast/" + oid + "/arquivar",
        data={"csrf_token": token(client)},
    )
    fake.records[row["transaction_id"]]["status"] = "paid"
    pickup_payments.reconcile(db, payments[0], datetime.now(TZ) + timedelta(minutes=1))
    actual = db.execute("SELECT * FROM pickup_payment_orders").fetchone()
    assert (
        actual["state"] == "paid" and actual["error_code"] == "archived_payment_review"
    )
    assert not db.execute("SELECT 1 FROM package_prepayments").fetchone()
    restarted = create_app(payments[0].config).test_client()
    staff(restarted)
    assert oid in restarted.get("/painel/retirada/cobrancas?arquivadas=1").text
    assert oid not in restarted.get("/painel/retirada/cobrancas").text
