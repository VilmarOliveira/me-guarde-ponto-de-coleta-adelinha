"""Prepayment uses simulated FastFlow; no real charges or emails."""

import json
from datetime import datetime, timedelta
import pytest
from test_customer_payments import (
    payments,
    package,
    staff,
    deliver,
    signed,
    create_pickup,
)
from test_plan import site, post_purchase
from test_app import token
from core import TZ
import plan_service as ps


def customer(client):
    with client.session_transaction() as s:
        s.clear()
        s.update(client_id=1, client_session_version=1)


def advance(client, ids, staff_mode=False, **extra):
    return client.post(
        "/painel/retirada/antecipar" if staff_mode else "/cliente/pacotes/pagar/gerar",
        data={"package_ids": ids, "csrf_token": token(client), **extra},
    )


def paid(client, db, fake, oid):
    row = db.execute(
        "SELECT * FROM pickup_payment_orders WHERE id=?", (oid,)
    ).fetchone()
    fake.records[row["transaction_id"]]["status"] = "paid"
    return signed(client, "/webhooks/fastflow/retiradas/" + oid, row["transaction_id"])


@pytest.mark.parametrize("by_staff", [False, True])
def test_advance_paid_per_package_and_zero_pickup(payments, by_staff):
    _, client, db, fake = payments
    first = package(db)
    second = package(db, number=2)
    fake.state = "approved"
    if by_staff:
        staff(client)
    response = advance(client, [first, second], by_staff)
    assert response.status_code == 200
    oid = response.json["id"]
    assert advance(client, [first, second], by_staff).json["id"] == oid
    assert (
        fake.creates == 1
        and db.execute("SELECT COUNT(*) n FROM package_prepayments").fetchone()["n"]
        == 0
    )
    assert paid(client, db, fake, oid).status_code == 200
    assert paid(client, db, fake, oid).status_code == 200
    entries = db.execute(
        "SELECT * FROM package_prepayments ORDER BY package_id"
    ).fetchall()
    assert len(entries) == 2 and sum(x["paid_cents"] for x in entries) == 980
    assert [x["settled_cents"] for x in entries] == [490, 490]
    assert (
        db.execute(
            "SELECT COUNT(*) n FROM packages WHERE status='aguardando_retirada'"
        ).fetchone()["n"]
        == 2
    )
    assert not db.execute("SELECT 1 FROM pickup_payment_reservations").fetchone()
    customer(client)
    assert (
        "Pago antecipadamente · Aguardando retirada"
        in client.get("/cliente?aba=pacotes").text
    )
    assert client.get("/cliente/pacotes/pagamentos/" + oid).status_code == 200
    assert advance(client, [first]).status_code == 400 and fake.creates == 1
    staff(client)
    assert deliver(client, [first, second], "Pix Fast").status_code == 302
    pickup = db.execute("SELECT * FROM pickups").fetchone()
    assert pickup["total_paid"] == 0 and pickup["payment_status"] == "zero_total"
    assert fake.creates == 1


def test_additional_days_only_remaining_and_physical_identity(payments):
    _, client, db, fake = payments
    pid = package(db)
    oid = advance(client, [pid]).json["id"]
    paid(client, db, fake, oid)
    db.execute(
        "UPDATE packages SET notified_at=? WHERE id=?",
        (ps.iso(datetime.now(TZ) - timedelta(days=5)), pid),
    )
    db.commit()
    staff(client)
    summary = client.post(
        "/painel/retirada/resumo",
        data={"package_ids": pid, "csrf_token": token(client)},
    ).json
    assert summary["total"] == 1 and not summary["fast_available"]
    assert create_pickup(client, [pid]).status_code == 400 and fake.creates == 1
    missing = client.post(
        "/retiradas",
        data={
            "package_ids": pid,
            "payment_method": "Dinheiro",
            "payment_confirmed": "yes",
            "csrf_token": token(client),
        },
    )
    assert (
        missing.status_code == 302
        and not db.execute("SELECT 1 FROM pickups").fetchone()
    )
    assert deliver(client, [pid], "Dinheiro").status_code == 302
    assert db.execute("SELECT total_paid FROM pickups").fetchone()["total_paid"] == 1
    assert db.execute("SELECT base_price FROM packages").fetchone()["base_price"] == 4.9


def test_pending_selection_shared_and_overlap_blocked(payments):
    _, client, db, fake = payments
    first = package(db)
    second = package(db, number=2)
    oid = advance(client, [first]).json["id"]
    staff(client)
    assert advance(client, [first], True).json["id"] == oid
    assert advance(client, [first, second], True).status_code == 400
    customer(client)
    assert advance(client, [first, second]).status_code == 400
    assert client.post(
        "/cliente/pacotes/pagar",
        data={"package_ids": [first], "csrf_token": token(client)},
    ).location.endswith(oid)
    assert fake.creates == 1


def test_client_reuses_pending_staff_charge_and_cannot_set_adjustments(payments):
    _, client, db, fake = payments
    pid = package(db)
    staff(client)
    oid = create_pickup(client, [pid], discount_amount="0.90").json["id"]
    customer(client)
    response = client.post(
        "/cliente/pacotes/pagar",
        data={"package_ids": [pid], "csrf_token": token(client)},
    )
    assert response.status_code == 302 and response.location.endswith(oid)
    paid(client, db, fake, oid)
    row = db.execute("SELECT * FROM package_prepayments").fetchone()
    assert row["paid_cents"] == 400 and row["settled_cents"] == 490
    staff(client)
    assert deliver(client, [pid], "Dinheiro").status_code == 302
    assert db.execute("SELECT total_paid FROM pickups").fetchone()["total_paid"] == 0


@pytest.mark.parametrize(
    "amount,allowed",
    [(0, False), (3.99, False), (4, True), (1000, True), (1000.01, False)],
)
def test_advance_limits_and_ignore_client_prices(payments, amount, allowed):
    _, client, db, fake = payments
    pid = package(db, base=amount)
    response = advance(
        client,
        [pid],
        discount_percent="100",
        surcharge_amount="900",
        surcharge_reason="forged",
    )
    assert response.status_code == (200 if allowed else 400)
    assert fake.creates == int(allowed)
    if allowed:
        assert db.execute("SELECT amount_cents FROM pickup_payment_orders").fetchone()[
            "amount_cents"
        ] == round(amount * 100)


def test_covered_package_nothing_due_and_no_credit_debited_twice(payments):
    _, client, db, fake = payments
    fake.state = "paid"
    post_purchase(client)
    staff(client)
    client.post(
        "/pacotes",
        data={
            "client_id": 1,
            "tracking": "ADVPLAN",
            "shelf": "A1",
            "width": 10,
            "height": 10,
            "length": 10,
            "weight": 1,
            "csrf_token": token(client),
        },
    )
    pid = db.execute("SELECT id FROM packages").fetchone()["id"]
    db.execute(
        "UPDATE packages SET status='aguardando_retirada',notified_at=?",
        (ps.iso(datetime.now(TZ)),),
    )
    db.commit()
    customer(client)
    page = client.post(
        "/cliente/pacotes/pagar",
        data={"package_ids": [pid], "csrf_token": token(client)},
    )
    assert "Incluído no plano · Nada a pagar" in page.text
    assert advance(client, [pid]).status_code == 400 and fake.creates == 1
    staff(client)
    assert deliver(client, [pid], "Pix Fast").status_code == 302
    assert (
        db.execute("SELECT used_credits FROM plan_cycles").fetchone()["used_credits"]
        == 1
    )


def test_owner_and_webhook_validation(payments):
    _, client, db, fake = payments
    pid = package(db)
    oid = advance(client, [pid]).json["id"]
    assert (
        client.post(
            "/webhooks/fastflow/retiradas/" + oid,
            data="{}",
            content_type="application/json",
        ).status_code
        == 401
    )
    row = db.execute("SELECT * FROM pickup_payment_orders").fetchone()
    fake.records[row["transaction_id"]]["status"] = "paid"
    fake.records[row["transaction_id"]]["amount"] = 999
    assert signed(
        client, "/webhooks/fastflow/retiradas/" + oid, row["transaction_id"]
    ).status_code in (400, 202)
    assert not db.execute("SELECT 1 FROM package_prepayments").fetchone()
    with client.session_transaction() as s:
        s.clear()
    assert client.get("/cliente/pacotes/pagamentos/" + oid).status_code == 302


def test_refund_requires_review_before_pickup(payments):
    _, client, db, fake = payments
    pid = package(db)
    oid = advance(client, [pid]).json["id"]
    paid(client, db, fake, oid)
    row = db.execute("SELECT * FROM pickup_payment_orders").fetchone()
    fake.records[row["transaction_id"]]["status"] = "refunded"
    signed(
        client,
        "/webhooks/fastflow/retiradas/" + oid,
        row["transaction_id"],
        status="refunded",
    )
    staff(client)
    deliver(client, [pid], "Dinheiro")
    assert not db.execute("SELECT 1 FROM pickups").fetchone()


def test_client_cannot_read_or_charge_another_clients_packages(payments):
    _, client, db, fake = payments
    pid = package(db)
    oid = advance(client, [pid]).json["id"]
    owner = dict(db.execute("SELECT * FROM clients WHERE id=1").fetchone())
    owner.pop("id")
    owner.update(
        name="Outro ficticio",
        email="other@example.test",
        cpf="39053344705",
        public_id="CLIPCA-2",
    )
    cid = db.execute(
        "INSERT INTO clients("
        + ",".join(owner)
        + ") VALUES("
        + ",".join("?" for _ in owner)
        + ") RETURNING id",
        tuple(owner.values()),
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as s:
        s.clear()
        s.update(client_id=cid, client_session_version=1)
    assert client.get("/cliente/pacotes/pagamentos/" + oid).status_code == 404
    assert (
        client.get("/cliente/pacotes/pagamentos/" + oid + "/status").status_code == 404
    )
    assert advance(client, [pid]).status_code == 404
    assert (
        client.post(
            "/cliente/pacotes/pagar",
            data={"package_ids": [pid], "csrf_token": token(client)},
        ).status_code
        == 404
    )
    assert fake.creates == 1


def test_preview_amount_changed_and_multiple_prepaid_cycles(payments):
    _, client, db, fake = payments
    pid = package(db)
    assert advance(client, [pid], expected_total="400").status_code == 400
    assert fake.creates == 0
    oid = advance(client, [pid], expected_total="490").json["id"]
    paid(client, db, fake, oid)
    db.execute(
        "UPDATE packages SET notified_at=? WHERE id=?",
        (ps.iso(datetime.now(TZ) - timedelta(days=11)), pid),
    )
    db.commit()
    oid2 = advance(client, [pid], expected_total="400").json["id"]
    assert oid2 != oid
    paid(client, db, fake, oid2)
    entries = db.execute(
        "SELECT * FROM package_prepayments WHERE package_id=?", (pid,)
    ).fetchall()
    assert sum(x["paid_cents"] for x in entries) == 890
    staff(client)
    assert deliver(client, [pid], "Dinheiro").status_code == 302
    assert db.execute("SELECT total_paid FROM pickups").fetchone()["total_paid"] == 0
    assert (
        db.execute("SELECT prepaid_amount FROM pickup_packages").fetchone()[
            "prepaid_amount"
        ]
        == 8.9
    )
    customer(client)
    assert (
        "Retirada concluída" in client.get("/cliente/pacotes/pagamentos/" + oid2).text
    )


def test_convert_already_paid_staff_charge_settles_without_waiting_for_poll(payments):
    _, client, db, fake = payments
    pid = package(db)
    staff(client)
    oid = create_pickup(client, [pid]).json["id"]
    paid(client, db, fake, oid)
    assert not db.execute("SELECT 1 FROM package_prepayments").fetchone()
    customer(client)
    assert advance(client, [pid]).json["id"] == oid
    assert (
        db.execute("SELECT paid_cents FROM package_prepayments").fetchone()[
            "paid_cents"
        ]
        == 490
    )
    assert fake.creates == 1


@pytest.mark.skipif(
    not __import__("os").environ.get("TEST_ADVANCE_DATABASE_URL"),
    reason="PostgreSQL isolado antecipado não configurado",
)
def test_postgres_concurrent_advance_and_settlement(monkeypatch):
    import os
    from concurrent.futures import ThreadPoolExecutor
    from app import create_app
    from database import DatabaseConnection
    from test_plan import CONFIG, FakeFlow
    from test_app import register
    import plan_routes

    app = create_app({**CONFIG, "DATABASE": os.environ["TEST_ADVANCE_DATABASE_URL"]})
    client = app.test_client()
    register(client)
    db = DatabaseConnection(app.config["DATABASE"])
    pid = package(db)
    fake = FakeFlow()
    original = fake.create

    def create(order, owner, card=None):
        result = original(order, owner, card)
        fake.records[result["id"]]["amount"] = order["amount_cents"] / 100
        return fake.records[result["id"]].copy()

    fake.create = create
    monkeypatch.setattr(plan_routes, "gateway", lambda _: fake)

    def issue(_):
        c = app.test_client()
        customer(c)
        response = advance(c, [pid])
        assert response.status_code == 200, response.text
        return response.json["id"]

    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(issue, range(3)))
    assert len(set(ids)) == 1 and fake.creates == 1
    customer(client)
    assert paid(client, db, fake, ids[0]).status_code == 200
    assert paid(client, db, fake, ids[0]).status_code == 200
    assert (
        db.execute("SELECT SUM(paid_cents) amount FROM package_prepayments").fetchone()[
            "amount"
        ]
        == 490
    )
    staff(client)
    deliver(client, [pid], "Pix Fast")
    row = db.execute("SELECT * FROM pickups").fetchone()
    assert row["total_paid"] == 0
    assert (
        db.execute("SELECT prepaid_amount FROM pickup_packages").fetchone()[
            "prepaid_amount"
        ]
        == 4.9
    )
    assert client.get(f"/comprovantes/{row['id']}/pacotes/{pid}.pdf").status_code == 200
    db.close()
