"""Shared local-payment fixtures. No provider credentials or HTTP simulator."""

from datetime import datetime
from types import SimpleNamespace
import pytest
from app import create_app
from core import TZ
from database import DatabaseConnection
from payment_domain import LocalSettings
import plan_service as ps
from test_app import register, token

START = datetime(2026, 1, 31, 12, tzinfo=TZ)
CONFIG = {
    "TESTING": True,
    "SECRET_KEY": "local-payment-fixture",
    "SITE_PUBLIC_ORIGIN": "https://example.test",
}


@pytest.fixture
def site(tmp_path, monkeypatch):
    app = create_app({**CONFIG, "DATABASE": str(tmp_path / "site.db")})
    client = app.test_client()
    register(client)
    db = DatabaseConnection(app.config["DATABASE"])
    db.execute(
        "UPDATE clients SET confirmation_channel='email',confirmation_destination=email,confirmation_validated_at=accepted_at"
    )
    db.commit()
    with client.session_transaction() as s:
        s.update(client_id=1, client_session_version=1)
    yield app, client, db, SimpleNamespace(
        creates=0, gets=0, records={}, state="pending"
    )
    db.close()


def person(db):
    return db.execute("SELECT * FROM clients WHERE id=1").fetchone()


def reserve(db, value=START, method="administrative"):
    return ps.reserve_order(
        db, person(db), "administrative", value, LocalSettings.from_config(CONFIG)
    )[0]


def create_cycle(db, fake=None, value=START, state="paid"):
    order = reserve(db, value)
    if state == "paid":
        ps.activate_order(db, order, value)
        db.execute(
            "UPDATE plan_orders SET state='manual_approved',manual_paid_at=?,local_method='Pix AI' WHERE id=?",
            (ps.iso(value), order["id"]),
        )
        db.commit()
    if state != "paid":
        db.execute(
            "UPDATE plan_orders SET state='pending',local_method='Pix AI' WHERE id=?",
            (order["id"],),
        )
        db.commit()
    return order, {"id": order["id"], "status": state}


def post_purchase(client, **extra):
    return client.post(
        "/cliente/plano/assinar",
        data={"plan_rules": "yes", "csrf_token": token(client), **extra},
    )


def insert_package(db, number, category="Pequeno"):
    result = db.execute(
        "INSERT INTO packages(client_id,public_id,tracking,width,height,length,weight,shelf,category,base_price,status,created_at) VALUES(1,?,?,10,10,10,1,'A1',?,4.9,'aguardando_aviso',?) RETURNING id",
        (f"PCTPCA-{number}", f"TRK{number}", category, ps.iso(START)),
    )
    return result.fetchone()["id"]
