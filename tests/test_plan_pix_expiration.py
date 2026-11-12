"""Plan Pix expiration and retry with a simulated provider, no real charges."""

from datetime import datetime, timedelta

import pytest

from core import TZ
from fastflow_gateway import GatewayError
from test_app import token
from test_plan import post_purchase, site
import plan_service as ps


def expired_plan(client, db, fake):
    url = post_purchase(client).location
    oid = url.rsplit("/", 1)[-1]
    past = (datetime.now(TZ) - timedelta(minutes=1)).isoformat()
    fake.records["1"]["qr_code_expires_at"] = past
    db.execute("UPDATE plan_orders SET expires_at=?,check_after=NULL WHERE id=?", (past, oid))
    db.commit()
    return oid, url


def test_plan_retry_button_creates_one_new_pix_and_reviews_late_payment(site):
    _, client, db, fake = site
    oid, url = expired_plan(client, db, fake)
    page = client.post(url + "/consultar", data={"csrf_token": token(client)}, follow_redirects=True)
    assert "Gerar novo Pix" in page.text and "data-plan-pix" not in page.text
    assert db.execute("SELECT cancel_state FROM plan_orders WHERE id=?", (oid,)).fetchone()["cancel_state"] == "closed"
    response = client.post(url + "/novo-pix", data={"csrf_token": token(client)})
    assert response.location != url and fake.creates == 2
    new_id = response.location.rsplit("/", 1)[-1]
    new = db.execute("SELECT * FROM plan_orders WHERE id=?", (new_id,)).fetchone()
    assert new["retry_of"] == oid and new["local_method"] == "Pix Fast"
    assert "data-plan-pix" in client.get(response.location).text
    assert client.post(url + "/novo-pix", data={"csrf_token": token(client)}).location == response.location
    assert fake.creates == 2
    old = fake.records["1"].copy()
    old["status"] = "paid"
    ps.apply_transaction(db, oid, old, datetime.now(TZ))
    old_row = db.execute("SELECT * FROM plan_orders WHERE id=?", (oid,)).fetchone()
    assert old_row["paid_at"] and old_row["error_code"] == "payment_after_cancellation"
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone()
    assert db.execute("SELECT state FROM plan_orders WHERE id=?", (new_id,)).fetchone()["state"] == "pending"
    newer = fake.records["2"].copy()
    newer["status"] = "paid"
    ps.apply_transaction(db, new_id, newer, datetime.now(TZ))
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles WHERE order_id=?", (new_id,)).fetchone()["n"] == 1


@pytest.mark.parametrize("case", ["unavailable", "identity", "future", "invalid_expiry", "changed_qr", "approved", "under_review", "paid"])
def test_plan_does_not_renew_unverified_or_paid_pix(site, monkeypatch, case):
    _, client, db, fake = site
    oid, url = expired_plan(client, db, fake)
    if case == "unavailable":
        def fail(tid):
            raise GatewayError()
        monkeypatch.setattr(fake, "get", fail)
    elif case == "identity":
        fake.records["1"]["amount"] = 999
    elif case == "future":
        fake.records["1"]["qr_code_expires_at"] = (datetime.now(TZ) + timedelta(hours=1)).isoformat()
    elif case == "invalid_expiry":
        fake.records["1"]["qr_code_expires_at"] = "invalid"
    elif case == "changed_qr":
        fake.records["1"]["qr_code_text"] = "new-code"
        fake.records["1"].pop("qr_code_expires_at")
    else:
        fake.records["1"]["status"] = case
    assert "Gerar novo Pix" not in client.get(url).text
    db.execute("UPDATE plan_orders SET checked_at=NULL,check_after=NULL WHERE id=?", (oid,))
    db.commit()
    assert "Gerar novo Pix" not in client.get(url).text
    if case in ("changed_qr", "invalid_expiry"):
        assert db.execute("SELECT expires_at FROM plan_orders WHERE id=?", (oid,)).fetchone()["expires_at"] is None
    client.post(url + "/novo-pix", data={"csrf_token": token(client)})
    assert fake.creates == 1
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == (1 if case == "paid" else 0)
