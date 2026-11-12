"""Expired QR renewal: simulated API only; never creates a real charge."""

from datetime import datetime, timedelta
import json

import payment_policies as policies

import pytest

from core import TZ
from fastflow_gateway import GatewayError
from test_app import token
from test_customer_payments import package, payments, signed
from test_plan import site


def create(client, pid, retry_id=""):
    return client.post(
        "/cliente/pacotes/pagar/gerar",
        data={"package_ids": [pid], "csrf_token": token(client), "retry_order_id": retry_id},
    )


def expired_order(client, db, fake):
    pid = package(db)
    result = create(client, pid).json
    past = (datetime.now(TZ) - timedelta(minutes=1)).isoformat()
    fake.records["1"]["qr_code_expires_at"] = past
    db.execute(
        "UPDATE pickup_payment_orders SET expires_at=?,check_after=NULL WHERE id=?",
        (past, result["id"]),
    )
    db.commit()
    return pid, result["id"], result["url"]


def test_expired_pending_renews_through_customer_button_and_late_payment_is_reviewed(payments):
    _, client, db, fake = payments
    pid, oid, url = expired_order(client, db, fake)
    page = client.post(url, data={"csrf_token": token(client)})
    assert "Gerar novo Pix" in page.text
    assert "data-plan-pix" not in page.text
    assert not db.execute("SELECT 1 FROM pickup_payment_reservations").fetchone()
    review = client.post(
        "/cliente/pacotes/pagar",
        data={"package_ids": [pid], "csrf_token": token(client)},
    )
    assert review.status_code == 200 and 'id="advance-create"' in review.text
    new = create(client, pid).json
    assert new["id"] != oid and fake.creates == 2
    assert create(client, pid).json["id"] == new["id"] and fake.creates == 2
    fake.records["1"]["status"] = "paid"
    assert signed(client, "/webhooks/fastflow/retiradas/" + oid, "1").status_code == 200
    old = db.execute("SELECT * FROM pickup_payment_orders WHERE id=?", (oid,)).fetchone()
    assert old["error_code"] == "late_payment_review" and old["paid_at"]
    assert not db.execute("SELECT 1 FROM package_prepayments").fetchone()
    assert db.execute("SELECT order_id FROM pickup_payment_reservations").fetchone()["order_id"] == new["id"]


@pytest.mark.parametrize("case", ["unavailable", "identity", "future", "invalid_expiry", "changed_qr", "under_review", "approved"])
def test_does_not_release_unverified_or_reviewed_payment(payments, monkeypatch, case):
    _, client, db, fake = payments
    pid, oid, url = expired_order(client, db, fake)
    if case == "unavailable":
        def unavailable(tid):
            raise GatewayError()
        monkeypatch.setattr(fake, "get", unavailable)
    elif case == "identity":
        fake.records["1"]["amount"] = 999
    elif case == "future":
        fake.records["1"]["qr_code_expires_at"] = (datetime.now(TZ) + timedelta(hours=1)).isoformat()
    elif case == "invalid_expiry":
        fake.records["1"]["qr_code_expires_at"] = "invalid"
    elif case == "changed_qr":
        fake.records["1"]["qr_code_text"] = "different-code"
        fake.records["1"].pop("qr_code_expires_at")
    else:
        fake.records["1"]["status"] = case
    page = client.get(url)
    assert "Gerar novo Pix" not in page.text
    db.execute("UPDATE pickup_payment_orders SET check_after=NULL WHERE id=?", (oid,))
    db.commit()
    assert "Gerar novo Pix" not in client.get(url).text
    if case in ("changed_qr", "invalid_expiry"):
        assert db.execute("SELECT expires_at FROM pickup_payment_orders WHERE id=?", (oid,)).fetchone()["expires_at"] is None
    assert db.execute("SELECT 1 FROM pickup_payment_reservations WHERE order_id=?", (oid,)).fetchone()
    assert create(client, pid).json["id"] == oid and fake.creates == 1


def test_paid_status_wins_over_expired_deadline(payments):
    _, client, db, fake = payments
    pid, oid, url = expired_order(client, db, fake)
    fake.records["1"]["status"] = "paid"
    page = client.get(url)
    assert "Gerar novo Pix" not in page.text
    assert db.execute("SELECT state FROM pickup_payment_orders WHERE id=?", (oid,)).fetchone()["state"] == "paid"
    assert create(client, pid).status_code == 400 and fake.creates == 1
    assert db.execute("SELECT 1 FROM package_prepayments").fetchone()


def cash_first(db, fast_enabled=True):
    rules = policies.defaults()
    for rule in rules:
        rule["priority"] = {"Dinheiro": 1, "Pix Fast": 2, "Pix AI": 3}[rule["method"]]
        if rule["method"] == "Pix Fast":
            rule["enabled"] = fast_enabled
    db.execute("UPDATE customer_payment_policies SET rules=? WHERE scope='advance'", (json.dumps(rules),))
    db.commit()


def test_retry_keeps_fast_when_admin_prioritizes_cash(payments):
    _, client, db, fake = payments
    pid, oid, url = expired_order(client, db, fake)
    assert "Gerar novo Pix" in client.get(url).text
    cash_first(db)
    response = client.post("/cliente/pacotes/pagar", data={
        "package_ids": [pid], "csrf_token": token(client), "retry_order_id": oid,
    })
    assert response.status_code == 200
    assert 'name="retry_order_id" value="' + oid + '"' in response.text
    assert "Gerar Pix Fast" in response.text
    assert "Pague com dinheiro" not in response.text
    result = create(client, pid, oid)
    assert result.status_code == 200 and fake.creates == 2
    row = db.execute("SELECT * FROM pickup_payment_orders WHERE id=?", (result.json["id"],)).fetchone()
    assert row["local_method"] == "Pix Fast" and row["transaction_id"] == "2"
    assert create(client, pid, oid).json["id"] == row["id"] and fake.creates == 2


@pytest.mark.parametrize("unavailable", ["disabled", "paused", "range", "not_configured", "paid_source", "wrong_packages", "foreign_source"])
def test_retry_never_falls_back_to_cash(payments, unavailable):
    app, client, db, fake = payments
    pid, oid, url = expired_order(client, db, fake)
    client.get(url)
    cash_first(db, fast_enabled=unavailable != "disabled")
    if unavailable == "paused":
        app.config["FASTFLOW_NEW_CHARGES_ENABLED"] = "0"
    elif unavailable == "not_configured":
        app.config["FASTFLOW_ENABLED"] = "0"
    elif unavailable == "range":
        rules = policies.rules(db, "advance")
        next(r for r in rules if r["method"] == "Pix Fast")["minimum"] = 5000
        db.execute("UPDATE customer_payment_policies SET rules=? WHERE scope='advance'", (json.dumps(rules),))
    elif unavailable == "paid_source":
        db.execute("UPDATE pickup_payment_orders SET paid_at=? WHERE id=?", (datetime.now(TZ).isoformat(), oid))
    elif unavailable == "wrong_packages":
        pid = package(db, number=2)
    elif unavailable == "foreign_source":
        # An authenticated different customer must not access the original order.
        db.execute("INSERT INTO clients(id,name,cpf,phone,terms_version,signature,accepted_at) VALUES(999,'Outro cliente','12345678909','16999999999','test','test',?)", (datetime.now(TZ).isoformat(),))
        db.execute("UPDATE pickup_payment_orders SET client_id=999 WHERE id=?", (oid,))
    db.commit()
    review = client.post("/cliente/pacotes/pagar", data={
        "package_ids": [pid], "csrf_token": token(client), "retry_order_id": oid,
    })
    assert review.status_code in (302, 404)
    assert 'id="advance-create"' not in review.text
    result = create(client, pid, oid)
    assert result.status_code in (400, 404) and fake.creates == 1
    assert db.execute("SELECT COUNT(*) n FROM pickup_payment_orders").fetchone()["n"] == 1


def test_normal_checkout_respects_cash_priority(payments):
    _, client, db, fake = payments
    pid = package(db)
    cash_first(db)
    result = create(client, pid)
    assert result.status_code == 200 and fake.creates == 0
    assert db.execute("SELECT local_method FROM pickup_payment_orders").fetchone()["local_method"] == "Dinheiro"


def test_retry_rechecks_fast_availability_after_review(payments):
    _, client, db, fake = payments
    pid, oid, url = expired_order(client, db, fake)
    client.get(url)
    review = client.post("/cliente/pacotes/pagar", data={
        "package_ids": [pid], "csrf_token": token(client), "retry_order_id": oid,
    })
    assert review.status_code == 200 and 'id="advance-create"' in review.text
    cash_first(db, fast_enabled=False)
    result = create(client, pid, oid)
    assert result.status_code == 400 and "Pix Fast indisponível" in result.json["error"]
    assert fake.creates == 1
    assert db.execute("SELECT COUNT(*) n FROM pickup_payment_orders").fetchone()["n"] == 1
