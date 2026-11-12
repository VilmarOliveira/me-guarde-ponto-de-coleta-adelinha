"""Expired QR renewal: simulated API only; never creates a real charge."""

from datetime import datetime, timedelta

import pytest

from core import TZ
from fastflow_gateway import GatewayError
from test_app import token
from test_customer_payments import package, payments, signed
from test_plan import site


def create(client, pid):
    return client.post(
        "/cliente/pacotes/pagar/gerar",
        data={"package_ids": [pid], "csrf_token": token(client)},
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
