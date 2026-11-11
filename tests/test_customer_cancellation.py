"""Customer cancellations use simulated provider calls and preserve audit history."""

import pytest
from test_plan import site, post_purchase, signed
from test_customer_payments import payments, package
from test_advance_payments import advance
from test_app import token
from fastflow_gateway import FastFlow, Settings, GatewayError
from test_plan import CONFIG


def test_customer_cancels_plan_and_immediately_requests_again(site, monkeypatch):
    app, client, db, fake = site
    post_purchase(client)
    old = dict(db.execute("SELECT * FROM plan_orders").fetchone())
    monkeypatch.setattr(
        fake,
        "cancel",
        lambda tid: fake.records[tid].update(status="cancelled"),
        raising=False,
    )
    path = f"/cliente/plano/pagamentos/{old['id']}/cancelar"
    page = client.get(f"/cliente/plano/pagamentos/{old['id']}")
    assert "Cancelar cobrança" in page.text
    response = client.post(path, data={"csrf_token": token(client)})
    assert response.status_code == 302 and response.location.endswith("/cliente/plano")
    saved = db.execute("SELECT * FROM plan_orders WHERE id=?", (old["id"],)).fetchone()
    assert saved["cancel_state"] == "closed" and saved["qr_text"] is None
    assert client.post(path, data={"csrf_token": token(client)}).status_code == 302
    post_purchase(client)
    assert fake.creates == 2
    assert db.execute("SELECT COUNT(*) n FROM plan_orders").fetchone()["n"] == 2
    # A delayed pending response must not reopen the cancelled cycle slot.
    fake.records[old["transaction_id"]]["status"] = "pending"
    pending = signed(
        client, old, {"transaction_id": old["transaction_id"], "status": "pending"}
    )
    assert pending.status_code == 200
    assert (
        db.execute(
            "SELECT cancel_state FROM plan_orders WHERE id=?", (old["id"],)
        ).fetchone()["cancel_state"]
        == "closed"
    )
    fake.records[old["transaction_id"]]["status"] = "paid"
    db.execute("UPDATE plan_orders SET check_after=NULL WHERE id=?", (old["id"],))
    db.commit()
    result = signed(
        client, old, {"transaction_id": old["transaction_id"], "status": "paid"}
    )
    assert result.status_code == 200, result.text
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 0
    assert (
        db.execute(
            "SELECT error_code FROM plan_orders WHERE id=?", (old["id"],)
        ).fetchone()["error_code"]
        == "payment_after_cancellation"
    )


def test_customer_cancel_failure_does_not_release_plan(site, monkeypatch):
    _, client, db, fake = site
    post_purchase(client)
    order = db.execute("SELECT * FROM plan_orders").fetchone()

    def rejected(tid):
        raise GatewayError(code="rejected", http_status=403)

    monkeypatch.setattr(fake, "cancel", rejected, raising=False)
    response = client.post(
        f"/cliente/plano/pagamentos/{order['id']}/cancelar",
        data={"csrf_token": token(client)},
    )
    assert response.status_code == 302
    post_purchase(client)
    assert fake.creates == 1
    assert (
        db.execute("SELECT cancel_state FROM plan_orders").fetchone()["cancel_state"]
        == "external_pending"
    )


def test_customer_cancels_packages_and_immediately_requests_again(payments):
    _, client, db, fake = payments
    pid = package(db)
    first = advance(client, [pid]).json["id"]
    path = f"/cliente/pacotes/pagamentos/{first}/cancelar"
    assert (
        "Cancelar cobrança" in client.get(f"/cliente/pacotes/pagamentos/{first}").text
    )
    assert client.post(path, data={"csrf_token": token(client)}).status_code == 302
    assert (
        db.execute("SELECT COUNT(*) n FROM pickup_payment_reservations").fetchone()["n"]
        == 0
    )
    second = advance(client, [pid]).json["id"]
    assert second != first and fake.creates == 2
    assert db.execute("SELECT COUNT(*) n FROM package_prepayments").fetchone()["n"] == 0


@pytest.mark.parametrize("kind", ["plan", "packages"])
def test_customer_cannot_cancel_another_owner(payments, kind):
    _, client, db, fake = payments
    if kind == "plan":
        post_purchase(client)
        oid = db.execute("SELECT id FROM plan_orders").fetchone()["id"]
        path = f"/cliente/plano/pagamentos/{oid}/cancelar"
    else:
        oid = advance(client, [package(db)]).json["id"]
        path = f"/cliente/pacotes/pagamentos/{oid}/cancelar"
    with client.session_transaction() as session:
        session["client_id"] = 999
    assert client.post(path, data={"csrf_token": token(client)}).status_code in (
        302,
        404,
    )
    assert fake.records["1"]["status"] == "pending"


def test_documented_put_fallback_rechecks_before_cancellation(monkeypatch):
    api = FastFlow(Settings.from_config(CONFIG))
    calls = []

    def request(method, endpoint, **kwargs):
        calls.append((method, endpoint, kwargs))
        if method == "DELETE":
            raise GatewayError(code="rejected", http_status=403)
        if method == "GET":
            return {"id": "123", "status": "pending"}
        return {"id": "123", "status": "cancelled"}

    monkeypatch.setattr(api, "request", request)
    api.cancel("123")
    assert [c[0] for c in calls] == ["DELETE", "GET", "PUT"]
    assert calls[-1][2] == {"payload": {"status": "cancelled"}}


def test_put_fallback_refuses_payment_found_during_cancellation(monkeypatch):
    api = FastFlow(Settings.from_config(CONFIG))

    def request(method, endpoint, **kwargs):
        if method == "DELETE":
            raise GatewayError(code="rejected", http_status=403)
        assert method == "GET"
        return {"id": "123", "status": "paid"}

    monkeypatch.setattr(api, "request", request)
    with pytest.raises(GatewayError):
        api.cancel("123")
