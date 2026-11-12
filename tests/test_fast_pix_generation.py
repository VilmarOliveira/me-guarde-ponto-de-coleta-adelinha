"""Regression checks for existing Pix across checkout and counter flows."""

from datetime import datetime, timedelta

import pytest

from core import TZ
from fastflow_gateway import GatewayError, pix_creation_details
from test_app import token
from test_customer_payments import admin_form, create_pickup, package, payments, staff
from test_plan import create_cycle, post_purchase, site


@pytest.mark.parametrize(
    "flow", ["plan", "renewal", "admin_plan", "customer_advance", "staff_advance", "pickup"]
)
def test_pix_reuses_valid_qr_and_renews_after_verified_expiration(payments, monkeypatch, flow):
    _, client, db, fake = payments
    if flow in ("plan", "renewal"):
        if flow == "renewal":
            create_cycle(db, fake, datetime.now(TZ) - timedelta(days=5))
            fake.state = "pending"
        create = lambda: post_purchase(client).location
        table = "plan_orders"
    elif flow == "admin_plan":
        staff(client)
        path = "/painel/configuracoes/plano/clientes/1/validar"
        create = lambda: client.post(path, data=admin_form(client, path, "Pix Fast")).location
        table = "plan_orders"
    else:
        pid = package(db)
        table = "pickup_payment_orders"
        if flow != "customer_advance":
            staff(client)
        if flow == "pickup":
            create = lambda: create_pickup(client, [pid]).json["url"]
        else:
            path = (
                "/cliente/pacotes/pagar/gerar" if flow == "customer_advance"
                else "/painel/retirada/antecipar"
            )
            create = lambda: client.post(
                path, data={"package_ids": [pid], "csrf_token": token(client)}
            ).json["url"]

    initial_creates = fake.creates
    original_get = fake.get

    def details_without_qr(tid):
        data = original_get(tid)
        data.pop("qr_code_text")
        data.pop("qr_code_expires_at")
        return data

    monkeypatch.setattr(fake, "get", details_without_qr)
    first_url = create()
    first_id = first_url.rsplit("/", 1)[-1]
    assert 'data-plan-pix' in client.get(first_url).text
    assert client.get(first_url + "/status").json["pix_available"] is True
    assert fake.creates == initial_creates + 1

    past = (datetime.now(TZ) - timedelta(minutes=1)).isoformat()
    db.execute(f"UPDATE {table} SET expires_at=?,check_after=NULL WHERE id=?", (past, first_id))
    db.commit()
    expired_page = client.get(first_url).text
    assert "data-plan-pix" not in expired_page
    assert client.get(first_url + "/status").json["pix_available"] is False
    assert db.execute(f"SELECT state FROM {table} WHERE id=?", (first_id,)).fetchone()["state"] == "expired"
    if flow in ("customer_advance", "plan", "renewal"):
        assert "Gerar novo Pix" in expired_page
        assert "Pix vencido. Você já pode solicitar um novo." in expired_page
    # All six flows release the local order even while Fast reports pending.
    assert fake.records[str(initial_creates + 1)]["status"] == "pending"
    second_url = create()
    assert second_url != first_url
    assert 'data-plan-pix' in client.get(second_url).text
    assert fake.creates == initial_creates + 2


def test_pix_does_not_borrow_expiry_from_a_different_qr():
    created = {"id": 1, "qr_code_text": "original", "qr_code_expires_at": "original-expiry"}
    details = {"id": "1", "status": "pending", "qr_code_text": "changed"}
    assert pix_creation_details(created, details) == details
    with pytest.raises(GatewayError, match="Fast"):
        pix_creation_details(created, {**details, "id": "2"})
