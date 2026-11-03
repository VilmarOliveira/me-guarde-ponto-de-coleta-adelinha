"""Isolated payment simulations. Never contacts FastFlow or sends real email."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
import requests
from app import create_app, fees
from core import TZ
from database import DatabaseConnection
from fastflow_gateway import Settings, FastFlow, GatewayError, money_matches
import plan_routes
import plan_service as ps
from plan_jobs import reconcile, reminders
from test_app import register, token, login

START = datetime(2026, 1, 31, 12, tzinfo=TZ)
CONFIG = {
    "TESTING": True,
    "SECRET_KEY": "plan-fixture-only",
    "FASTFLOW_ENABLED": "1",
    "FASTFLOW_API_KEY": "fixture-key-not-valid",
    "FASTFLOW_WEBHOOK_SECRET": "fixture-secret-not-valid-0000000000",
    "FASTFLOW_PUBLIC_ORIGIN": "https://example.test",
}


class FakeFlow:
    def __init__(self):
        self.records = {}
        self.creates = 0
        self.gets = 0
        self.failure = None
        self.state = "pending"
        self.method = "pix"

    def provider(self):
        return True

    def card_available(self):
        return True

    def create(self, order, owner, card=None):
        self.creates += 1
        data = {
            "id": str(self.creates),
            "amount": 33.90,
            "payment_provider": "fastflow",
            "payment_method": order["method"],
            "status": self.state,
            "notification_url": order["callback_url"],
            "user": {"cpf_cnpj": owner["cpf"]},
            "qr_code_text": "000201-fixture-pix",
            "qr_code_expires_at": "2026-11-30 10:00:00",
        }
        if card:
            data.update(
                card={"brand": "visa", "last4": card["number"][-4:]},
                receivable_status="scheduled",
            )
        self.records[data["id"]] = data
        if self.failure:
            raise self.failure
        return data.copy()

    def get(self, tid):
        self.gets += 1
        return self.records[str(tid)].copy()

    def find(self, order):
        return next(
            (
                d.copy()
                for d in self.records.values()
                if d["notification_url"] == order["callback_url"]
            ),
            None,
        )


@pytest.fixture
def site(tmp_path, monkeypatch):
    path = str(tmp_path / "plan.db")
    app = create_app({**CONFIG, "DATABASE": path})
    client = app.test_client()
    register(client)
    db = DatabaseConnection(path)
    db.execute(
        "UPDATE clients SET confirmation_channel='email',confirmation_destination=email,confirmation_validated_at=accepted_at"
    )
    db.commit()
    with client.session_transaction() as sess:
        sess.update(client_id=1, client_session_version=1)
    fake = FakeFlow()
    monkeypatch.setattr(plan_routes, "gateway", lambda app: fake)
    yield app, client, db, fake
    db.close()


def person(db):
    return db.execute("SELECT * FROM clients WHERE id=1").fetchone()


def reserve(db, value=START, method="pix"):
    return ps.reserve_order(
        db, person(db), method, value, Settings.from_config(CONFIG)
    )[0]


def create_cycle(db, fake, value=START, state="paid"):
    order = reserve(db, value)
    fake.state = state
    data = fake.create(order, person(db))
    ps.apply_transaction(db, order["id"], data, value)
    return order, data


def post_purchase(client, **extra):
    return client.post(
        "/cliente/plano/assinar",
        data={"csrf_token": token(client), "plan_rules": "yes", **extra},
    )


def signed(client, order, payload, secret=CONFIG["FASTFLOW_WEBHOOK_SECRET"]):
    raw = json.dumps(payload).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/fastflow/pedidos/" + order["id"],
        data=raw,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": "sha256=" + sig,
        },
    )


def test_checkout_fixed_price_duplicate_click_and_visibility(site):
    app, client, db, fake = site
    assert not plan_routes.eligible(db, person(db), datetime.now(TZ))
    response = post_purchase(client, amount="0.01", status="paid", method="pix")
    assert response.status_code == 302
    for _ in range(3):
        assert post_purchase(client).location == response.location
    assert fake.creates == 1
    order = db.execute("SELECT * FROM plan_orders").fetchone()
    assert order["amount_cents"] == 3390
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone()
    page = client.get(response.location)
    assert page.status_code == 200 and "Copiar código Pix" in page.text
    assert client.get("/cliente/plano").status_code == 200
    assert (
        client.post("/cliente/plano/assinar", data={"plan_rules": "yes"}).status_code
        == 400
    )
    assert client.get("/painel/configuracoes/plano").status_code == 302


@pytest.mark.parametrize(
    "patch",
    [
        {"amount": 33.89},
        {"payment_provider": "other"},
        {"notification_url": "https://example.test/other"},
        {"user": {"cpf_cnpj": "11111111111"}},
        {"payment_method": "card"},
    ],
)
def test_mismatched_transaction_never_grants(site, patch):
    _, _, db, fake = site
    order = reserve(db)
    data = fake.create(order, person(db))
    data.update(status="paid", **patch)
    with pytest.raises(GatewayError):
        ps.apply_transaction(db, order["id"], data, START)
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone()


def test_approved_not_paid_and_webhook_requires_fresh_api(site):
    _, client, db, fake = site
    order, data = create_cycle(db, fake, state="approved")
    assert (
        signed(
            client, order, {"transaction_id": data["id"], "status": "paid"}
        ).status_code
        == 200
    )
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone()
    fake.records[data["id"]]["status"] = "paid"
    for event in ("approved", "paid", "paid", "pending"):
        assert (
            signed(
                client, order, {"transaction_id": data["id"], "status": event}
            ).status_code
            == 200
        )
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1
    assert (
        db.execute(
            "SELECT COUNT(*) n FROM plan_credit_ledger WHERE kind='grant'"
        ).fetchone()["n"]
        == 1
    )
    assert db.execute("SELECT state FROM plan_orders").fetchone()["state"] == "paid"
    assert (
        client.post(
            "/webhooks/fastflow", json={"transaction_id": data["id"], "status": "paid"}
        ).status_code
        == 401
    )
    assert (
        signed(
            client, order, {"transaction_id": data["id"]}, secret="wrong"
        ).status_code
        == 401
    )


def test_webhook_before_create_response_and_pending_reconciliation(site):
    app, client, db, fake = site
    order = reserve(db, datetime.now(TZ))
    data = fake.create(order, person(db))
    fake.records[data["id"]]["status"] = "paid"
    assert (
        signed(
            client,
            order,
            {
                "event": "transaction.paid",
                "data": {"transaction_id": data["id"], "status": "paid"},
            },
        ).status_code
        == 200
    )
    assert (
        db.execute("SELECT transaction_id FROM plan_orders").fetchone()[
            "transaction_id"
        ]
        == data["id"]
    )
    ps.apply_transaction(
        db, order["id"], data, datetime.now(TZ)
    )  # stale pending response
    assert db.execute("SELECT state FROM plan_orders").fetchone()["state"] == "paid"
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1


def test_timeout_never_reposts_and_reconciles_without_webhook(site):
    app, client, db, fake = site
    fake.failure = GatewayError(uncertain=True)
    first = post_purchase(client)
    assert first.status_code == 302
    assert (
        db.execute("SELECT state FROM plan_orders").fetchone()["state"] == "uncertain"
    )
    post_purchase(client)
    assert fake.creates == 1
    fake.failure = None
    fake.records["1"]["status"] = "paid"
    assert reconcile(db, app.config, datetime.now(TZ) + timedelta(minutes=3)) >= 1
    assert db.execute("SELECT state FROM plan_orders").fetchone()["state"] == "paid"
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1


def test_monthly_cycle_early_renewal_and_late_restart(site):
    _, _, db, fake = site
    order, data = create_cycle(db, fake)
    current, _, _ = ps.cycles(db, 1, START)
    assert ps.parse(current["ends_at"]) == datetime(2026, 2, 28, 12, tzinfo=TZ)
    renewed, renewal = create_cycle(db, fake, START + timedelta(days=1))
    current, future, _ = ps.cycles(db, 1, START + timedelta(days=1))
    assert future["granted_at"] is None
    assert ps.parse(future["ends_at"]) == datetime(2026, 3, 31, 12, tzinfo=TZ)
    assert reserve(db, START + timedelta(days=2)) is None
    db.commit()
    db.begin_write()
    ps.lock_account(db, 1, START)
    ps.grant_due(db, 1, ps.parse(future["starts_at"]))
    db.commit()
    assert (
        db.execute(
            "SELECT COUNT(*) n FROM plan_credit_ledger WHERE kind='grant'"
        ).fetchone()["n"]
        == 2
    )
    later = datetime(2026, 4, 5, 13, tzinfo=TZ)
    create_cycle(db, fake, later)
    current, _, _ = ps.cycles(db, 1, later)
    assert ps.parse(current["starts_at"]) == later and current["used_credits"] == 0


def insert_package(db, number, category="Pequeno"):
    result = db.execute(
        "INSERT INTO packages(client_id,public_id,tracking,width,height,length,weight,shelf,category,base_price,status,created_at) VALUES(1,?,?,10,10,10,1,'A1',?,4.9,'aguardando_aviso',?) RETURNING id",
        (f"PCTPCA-{number}", f"TRK{number}", category, ps.iso(START)),
    )
    return result.fetchone()["id"]


def test_credit_consumption_mixed_no_accumulation_and_no_retroactivity(site):
    _, _, db, fake = site
    old = insert_package(db, 0)
    db.commit()
    create_cycle(db, fake)
    assert ps.coverage(db, old) is None
    for n in range(1, 8):
        db.commit()
        db.begin_write()
        pid = insert_package(db, n)
        covered = ps.consume(
            db, 1, pid, f"PCTPCA-{n}", "Grande" if n <= 4 else "Pequeno", 4.9, START
        )
        db.commit()
        assert covered == (n <= 6)
    current, _, _ = ps.cycles(db, 1, START)
    assert current["used_credits"] == 10
    assert (
        db.execute("SELECT base_price FROM packages WHERE id=?", (old,)).fetchone()[
            "base_price"
        ]
        == 4.9
    )
    covered = db.execute("SELECT * FROM packages WHERE id=2").fetchone()
    assert covered["base_price"] == 0
    updated = {
        **dict(covered),
        "notified_at": ps.iso(START),
        "picked_at": ps.iso(START + timedelta(days=5)),
    }
    amounts = fees(updated)
    assert amounts["base"] == 0 and amounts["late_fee"] > 0


def test_refund_and_out_of_order_never_restore_or_reprice_packages(site):
    _, _, db, fake = site
    order, data = create_cycle(db, fake)
    db.begin_write()
    pid = insert_package(db, 1)
    assert ps.consume(db, 1, pid, "PCTPCA-1", "Pequeno", 4.9, START)
    db.commit()
    data["status"] = "refunded"
    ps.apply_transaction(db, order["id"], data, START + timedelta(days=1))
    data["status"] = "paid"
    ps.apply_transaction(db, order["id"], data, START + timedelta(days=2))
    cycle = db.execute("SELECT * FROM plan_cycles").fetchone()
    assert cycle["suspended"] == 1 and cycle["review_required"] == 1
    assert db.execute("SELECT base_price FROM packages").fetchone()["base_price"] == 0
    assert db.execute("SELECT state FROM plan_orders").fetchone()["state"] == "refunded"


def test_reminders_twice_deduplicated_and_suppressed_after_renewal(site):
    app, _, db, fake = site
    create_cycle(db, fake)
    calls = []
    send = lambda *a, **k: (calls.append((a, k)) or (True, "sent"))
    day = datetime(2026, 2, 27, 8, tzinfo=TZ)
    assert reminders(db, app.config, day, send) == 1
    assert reminders(db, app.config, day, send) == 0
    assert (
        "R$ 33,90" in calls[0][0][2] and "Renovar meu plano" in calls[0][1]["html_body"]
    )
    create_cycle(db, fake, day + timedelta(hours=1))
    assert reminders(db, app.config, day + timedelta(days=1), send) == 0
    assert db.execute("SELECT COUNT(*) n FROM plan_reminders").fetchone()["n"] == 1


def test_reminders_final_day_and_uncertain_not_retried(site):
    app, _, db, fake = site
    create_cycle(db, fake)
    calls = []

    def fail(*a, **k):
        calls.append(1)
        raise RuntimeError("fixture delivery uncertain")

    day = datetime(2026, 2, 28, 8, tzinfo=TZ)
    assert reminders(db, app.config, day, fail) == 0
    assert reminders(db, app.config, day, fail) == 0 and len(calls) == 1
    assert (
        db.execute("SELECT state FROM plan_reminders").fetchone()["state"]
        == "delivery_uncertain"
    )


def test_card_declined_no_sensitive_persistence_and_disabled_gate(site, caplog,monkeypatch):
    app, client, db, fake = site
    fields = {
        "method": "card",
        "card_number": "4111111111111111",
        "card_cvv": "765",
        "card_expiration": "12/2030",
        "card_holder": "Fixture Customer",
    }
    assert post_purchase(client, **fields).status_code == 302 and fake.creates == 0
    monkeypatch.setattr("fastflow_gateway.CARD_CHECKOUT_AVAILABLE",True)  # Legacy adapter coverage only.
    app.config.update(FASTFLOW_CARD_ENABLED="1", FASTFLOW_CARD_PCI_READY="1")
    fake.failure = GatewayError("Cartão recusado.", code="declined")
    response = post_purchase(client, **fields)
    assert response.status_code == 302
    order = db.execute("SELECT * FROM plan_orders").fetchone()
    assert order["state"] == "declined"
    assert (
        fields["card_number"] not in str(dict(order))
        and fields["card_number"] not in caplog.text
    )
    assert fields["card_number"] not in client.get(response.location).text


def test_card_paid_scheduled_is_not_settled(site):
    _, _, db, fake = site
    order = reserve(db, method="card")
    fake.state = "paid"
    data = fake.create(order, person(db), {"number": "4111111111111111"})
    ps.apply_transaction(db, order["id"], data, START)
    stored = db.execute("SELECT * FROM plan_orders").fetchone()
    assert (
        stored["state"] == "paid"
        and stored["settlement_state"] == "scheduled"
        and stored["card_last4"] == "1111"
    )
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1


def test_incomplete_and_inactive_clients_cannot_purchase(site):
    _, client, db, fake = site
    db.execute("UPDATE clients SET residential_street='' WHERE id=1")
    db.commit()
    assert post_purchase(client).status_code == 302 and fake.creates == 0
    db.execute("UPDATE clients SET active=0 WHERE id=1")
    db.commit()
    assert client.get("/cliente/plano").status_code == 302
    assert post_purchase(client).status_code == 302 and fake.creates == 0


def test_order_isolation_and_admin_refresh(site):
    _, client, db, fake = site
    order, data = create_cycle(db, fake)
    with client.session_transaction() as sess:
        sess.clear()
        sess.update(staff=True)
    assert client.get("/painel/configuracoes/plano").status_code == 200
    page = client.post(
        "/painel/configuracoes/plano/pagamentos/" + order["id"] + "/consultar",
        data={"csrf_token": token(client), "status": "paid"},
        follow_redirects=True,
    )
    assert page.status_code == 200 and "Transação" in page.text
    with client.session_transaction() as sess:
        sess.clear()
    assert client.get("/cliente/plano/pagamentos/" + order["id"]).status_code == 302
    assert (
        client.post(
            "/painel/configuracoes/plano/pagamentos/" + order["id"] + "/consultar",
            data={"csrf_token": token(client)},
        ).status_code
        == 302
    )


def test_receiving_integration_preserves_zero_base_edit_and_delete_guard(site):
    _, client, db, fake = site
    create_cycle(db, fake, datetime.now(TZ))
    with client.session_transaction() as sess:
        sess.clear()
        sess.update(staff=True)
    fields = {
        "client_id": 1,
        "width": 10,
        "height": 10,
        "length": 10,
        "weight": 1,
        "tracking": "PKG100",
        "shelf": "A1",
        "csrf_token": token(client),
    }
    assert client.post("/pacotes", data=fields).status_code == 302
    pkg = db.execute("SELECT * FROM packages").fetchone()
    assert pkg["base_price"] == 0
    assert (
        client.post(
            "/painel/pacotes/1/editar", data={**fields, "shelf": "A2"}
        ).status_code
        == 302
    )
    assert db.execute("SELECT base_price FROM packages").fetchone()["base_price"] == 0
    assert (
        client.post(
            "/painel/pacotes/1/editar",
            data={**fields, "width": 50, "height": 50, "length": 40, "weight": 10},
        ).status_code
        == 302
    )
    assert (
        db.execute("SELECT category FROM packages").fetchone()["category"] == "Pequeno"
    )
    assert (
        client.post(
            "/painel/pacotes/1/excluir",
            data={"confirmation": pkg["public_id"], "csrf_token": token(client)},
        ).status_code
        == 302
    )
    assert db.execute("SELECT COUNT(*) n FROM packages").fetchone()["n"] == 1
    assert (
        db.execute("SELECT used_credits FROM plan_cycles").fetchone()["used_credits"]
        == 1
    )


def test_simultaneous_payments_grant_once(site):
    app, _, db, fake = site
    order = reserve(db)
    data = fake.create(order, person(db))
    data["status"] = "paid"
    db.commit()

    def apply(_):
        connection = DatabaseConnection(app.config["DATABASE"])
        try:
            ps.apply_transaction(connection, order["id"], data, START)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(apply, range(4)))
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1
    assert db.execute("SELECT COUNT(*) n FROM plan_credit_ledger").fetchone()["n"] == 1


def test_adapter_provider_amount_timeout_and_no_raw_errors(monkeypatch):
    cfg = Settings.from_config(CONFIG)
    api = FastFlow(cfg)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        assert kwargs["allow_redirects"] is False and kwargs["timeout"] == (5, 15)
        if url.endswith("/auth/me"):
            return type(
                "Reply",
                (),
                {
                    "status_code": 200,
                    "json": lambda self: {
                        "success": True,
                        "data": {"api_provider": "fastflow"},
                    },
                },
            )()
        raise requests.Timeout("SENSITIVE REQUEST BODY")

    monkeypatch.setattr(requests, "request", request)
    with pytest.raises(GatewayError) as failure:
        api.create(
            {"id": "fixture", "method": "pix"},
            {"name": "Fixture", "cpf": "529.982.247-25"},
        )
    assert failure.value.uncertain and "SENSITIVE" not in str(failure.value)
    assert (
        calls[-1][2]["json"]["amount"] == 33.90
        and "payment_provider" not in calls[-1][2]["json"]
    )
    assert calls[-1][2]["json"]["user"]["cpf_cnpj"] == "52998224725"
    assert (
        money_matches("33.900")
        and not money_matches("33.901")
        and not money_matches("NaN")
    )


@pytest.mark.parametrize("terminal", ["expired", "cancelled"])
def test_new_attempt_after_terminal_status_without_double_grant(site, terminal):
    _, _, db, fake = site
    order, data = create_cycle(db, fake, state=terminal)
    second = reserve(db, START + timedelta(minutes=2))
    assert second["id"] != order["id"]
    newer = fake.create(second, person(db))
    newer["status"] = "paid"
    ps.apply_transaction(db, second["id"], newer, START + timedelta(minutes=2))
    data["status"] = "paid"
    with pytest.raises(GatewayError, match="mesma contratação"):
        ps.apply_transaction(db, order["id"], data, START + timedelta(minutes=3))
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1


def test_optional_future_credits_unavailable_until_cycle_start(site):
    _, _, db, fake = site
    create_cycle(db, fake)
    db.execute("UPDATE plan_cycles SET used_credits=10")
    db.commit()
    create_cycle(db, fake, START + timedelta(days=1))
    db.begin_write()
    pid = insert_package(db, 99)
    assert not ps.consume(
        db, 1, pid, "PCTPCA-99", "Pequeno", 4.9, START + timedelta(days=2)
    )
    db.commit()
    current, future, _ = ps.cycles(db, 1, START + timedelta(days=2))
    assert future["granted_at"] is None and future["used_credits"] == 0


def test_actual_other_client_cannot_read_order(site):
    app, client, db, fake = site
    order, data = create_cycle(db, fake)
    row = dict(person(db))
    row.update(cpf="39053344705", email="other@example.com", public_id="CLIPCA-2")
    row.pop("id")
    columns = ",".join(row)
    marks = ",".join("?" for _ in row)
    oid = db.execute(
        f"INSERT INTO clients({columns}) VALUES({marks}) RETURNING id",
        tuple(row.values()),
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as sess:
        sess.clear()
        sess.update(client_id=oid, client_session_version=1)
    assert client.get("/cliente/plano/pagamentos/" + order["id"]).status_code == 404
    assert (
        client.get("/cliente/plano/pagamentos/" + order["id"] + "/status").status_code
        == 404
    )
    assert (
        client.post(
            "/cliente/plano/pagamentos/" + order["id"] + "/consultar",
            data={"csrf_token": token(client)},
        ).status_code
        == 404
    )


def test_manager_cannot_access_financial_settings(site):
    _, client, db, _ = site
    staff = db.execute(
        "INSERT INTO staff_users(name,email,password_hash,role,can_settings,created_at) VALUES('Fixture manager','manager@example.test','fixture','gerente',1,?) RETURNING id",
        (ps.iso(START),),
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as sess:
        sess.clear()
        sess.update(staff=True, staff_user_id=staff, staff_session_version=1)
    assert client.get("/painel/configuracoes/plano").status_code == 403
    assert (
        client.post(
            "/painel/configuracoes/plano/verificar-provedor",
            data={"csrf_token": token(client)},
        ).status_code
        == 403
    )


def test_paid_under_review_suspends_new_credits_preserves_receiving_history(site):
    _, _, db, fake = site
    order, data = create_cycle(db, fake)
    data["status"] = "under_review"
    ps.apply_transaction(db, order["id"], data, START + timedelta(days=1))
    cycle = db.execute("SELECT * FROM plan_cycles").fetchone()
    assert cycle["suspended"] and cycle["review_required"]


def test_simultaneous_receiving_cannot_overdraw_last_credit(site):
    app, _, db, fake = site
    create_cycle(db, fake)
    db.execute("UPDATE plan_cycles SET used_credits=9")
    db.commit()

    def consume(number):
        conn = DatabaseConnection(app.config["DATABASE"])
        try:
            conn.begin_write()
            ps.lock_account(conn, 1, START)
            pid = insert_package(conn, number)
            covered = ps.consume(
                conn, 1, pid, f"PCTPCA-{number}", "Pequeno", 4.9, START
            )
            conn.commit()
            return covered
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        result = list(pool.map(consume, [101, 102, 103]))
    assert sum(result) == 1
    assert (
        db.execute("SELECT used_credits FROM plan_cycles").fetchone()["used_credits"]
        == 10
    )


def test_api_provider_and_feature_gates(monkeypatch):
    cfg = Settings.from_config(CONFIG)

    def request(*a, **k):
        return type(
            "Reply",
            (),
            {
                "status_code": 200,
                "json": lambda self: {
                    "success": True,
                    "data": {"api_provider": "other"},
                },
            },
        )()

    monkeypatch.setattr(requests, "request", request)
    with pytest.raises(GatewayError, match="tipo fastflow"):
        FastFlow(cfg).provider()
    assert not FastFlow(cfg).card_available()
    assert not Settings.from_config({**CONFIG, "FASTFLOW_ENABLED": "0"}).ready
    assert not Settings.from_config(
        {**CONFIG, "FASTFLOW_PUBLIC_ORIGIN": "http://example.test"}
    ).ready
    assert not Settings.from_config(
        {**CONFIG, "FASTFLOW_PUBLIC_ORIGIN": "https://example.test/#fragment"}
    ).ready


def test_gateway_get_checks_id_and_find_never_retries_post(monkeypatch):
    api = FastFlow(Settings.from_config(CONFIG))
    calls = []

    def reply(method, path, **kwargs):
        calls.append((method, path))
        return (
            {"id": "2"}
            if path.endswith("/1")
            else {"transactions": [], "pagination": {"total_pages": 1}}
        )

    monkeypatch.setattr(api, "request", reply)
    with pytest.raises(GatewayError):
        api.get("1")
    assert (
        api.find({"created_at": ps.iso(START), "method": "pix", "id": "fixture"})
        is None
    )
    assert all(c[0] == "GET" for c in calls)


@pytest.mark.skipif(
    not __import__("os").environ.get("TEST_DATABASE_URL"),
    reason="PostgreSQL isolado não configurado",
)
def test_postgresql_atomic_activation_and_concurrent_last_credit():
    import os, uuid

    path = os.environ["TEST_DATABASE_URL"]
    app = create_app({**CONFIG, "DATABASE": path})
    db = DatabaseConnection(path)
    stamp = ps.iso(START)
    name = "Plano fixture " + uuid.uuid4().hex
    cid = db.execute(
        "INSERT INTO clients(name,cpf,phone,terms_version,terms_text,signature,accepted_at) VALUES(?,'00000000000','00000000000','fixture','fixture','fixture',?) RETURNING id",
        (name, stamp),
    ).fetchone()["id"]
    db.commit()
    owner = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    order = ps.reserve_order(db, owner, "pix", START, Settings.from_config(CONFIG))[0]
    data = {
        "id": str(cid + 100000),
        "amount": "33.90",
        "payment_provider": "fastflow",
        "payment_method": "pix",
        "status": "paid",
        "notification_url": order["callback_url"],
    }

    def apply(_):
        conn = DatabaseConnection(path)
        try:
            ps.apply_transaction(conn, order["id"], data, START)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(apply, range(4)))
    assert (
        db.execute(
            "SELECT COUNT(*) n FROM plan_cycles WHERE client_id=?", (cid,)
        ).fetchone()["n"]
        == 1
    )
    db.execute("UPDATE plan_cycles SET used_credits=9 WHERE client_id=?", (cid,))
    db.commit()

    def receive(number):
        conn = DatabaseConnection(path)
        try:
            conn.begin_write()
            ps.lock_account(conn, cid, START)
            pid = conn.execute(
                "INSERT INTO packages(client_id,tracking,width,height,length,weight,shelf,category,base_price,status,created_at) VALUES(?,?,10,10,10,1,'A1','Pequeno',4.9,'aguardando_aviso',?) RETURNING id",
                (cid, name + str(number), stamp),
            ).fetchone()["id"]
            result = ps.consume(conn, cid, pid, "FIXTURE", "Pequeno", 4.9, START)
            conn.commit()
            return result
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(receive, range(3)))
    assert sum(results) == 1
    assert (
        db.execute(
            "SELECT used_credits FROM plan_cycles WHERE client_id=?", (cid,)
        ).fetchone()["used_credits"]
        == 10
    )
    db.close()


def test_card_summary_requires_detail_get_before_grant(site, monkeypatch):
    app, client, db, fake = site
    monkeypatch.setattr("fastflow_gateway.CARD_CHECKOUT_AVAILABLE",True)  # Legacy adapter coverage only.
    app.config.update(FASTFLOW_CARD_ENABLED="1", FASTFLOW_CARD_PCI_READY="1")
    fake.state = "paid"
    original = fake.create

    def summary(order, owner, card):
        data = original(order, owner, card)
        return {k: v for k, v in data.items() if k not in ("notification_url", "user")}

    monkeypatch.setattr(fake, "create", summary)
    response = post_purchase(
        client,
        method="card",
        card_number="4111111111111111",
        card_holder="Fixture Customer",
        card_expiration="12/2030",
        card_cvv="123",
    )
    assert response.status_code == 302 and fake.gets >= 1
    assert db.execute("SELECT state FROM plan_orders").fetchone()["state"] == "paid"
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1


def test_persistent_shared_api_budget(site):
    _, _, db, _ = site
    for n in range(15):
        ps.claim_api_slot(db, "GET", "/transactions/" + str(n + 1), START)
    with pytest.raises(GatewayError, match="Limite"):
        ps.claim_api_slot(db, "GET", "/transactions", START)
    ps.claim_api_slot(db, "GET", "/transactions/1", START + timedelta(minutes=1))
    for _ in range(4):
        ps.claim_api_slot(db, "POST", "/transactions", START)
    with pytest.raises(GatewayError):
        ps.claim_api_slot(db, "POST", "/transactions", START)


def test_pickup_does_not_reconsume_or_charge_base_after_expiry(site):
    from test_app import signature

    _, client, db, fake = site
    create_cycle(db, fake)
    db.begin_write()
    pid = insert_package(db, 1)
    assert ps.consume(db, 1, pid, "PCTPCA-1", "Pequeno", 4.9, START)
    db.commit()
    with client.session_transaction() as sess:
        sess.clear()
        sess.update(staff=True)
    assert (
        client.post("/pacotes/1/avisar", data={"csrf_token": token(client)}).status_code
        == 302
    )
    before = db.execute("SELECT COUNT(*) n FROM plan_credit_ledger").fetchone()["n"]
    result = client.post(
        "/retiradas",
        data={
            "package_ids": str(pid),
            "receiver_name": "Cliente Teste",
            "receiver_document": "52998224725",
            "document_type": "CPF",
            "payment_method": "Dinheiro",
            "payment_confirmed": "yes",
            "pickup_signature": signature(),
            "csrf_token": token(client),
        },
    )
    assert result.status_code == 302
    assert (
        db.execute("SELECT status FROM packages WHERE id=?", (pid,)).fetchone()[
            "status"
        ]
        == "retirado"
    )
    assert (
        db.execute("SELECT COUNT(*) n FROM plan_credit_ledger").fetchone()["n"]
        == before
    )
    assert (
        db.execute(
            "SELECT service_amount FROM pickup_packages WHERE package_id=?", (pid,)
        ).fetchone()["service_amount"]
        == 0
    )


def test_replacement_of_refunded_future_cycle_is_selected(site):
    _, _, db, fake = site
    create_cycle(db, fake)
    order, data = create_cycle(db, fake, START + timedelta(days=1))
    data["status"] = "refunded"
    ps.apply_transaction(db, order["id"], data, START + timedelta(days=2))
    replacement, _ = create_cycle(db, fake, START + timedelta(days=3))
    _, future, _ = ps.cycles(db, 1, START + timedelta(days=4))
    assert future["order_id"] == replacement["id"] and not future["suspended"]
    assert reserve(db, START + timedelta(days=4)) is None


def test_restart_preserves_rules_payment_cycle_and_credits(site):
    app, client, db, fake = site
    order, _ = create_cycle(db, fake, datetime.now(TZ))
    before = dict(
        db.execute("SELECT * FROM plan_orders WHERE id=?", (order["id"],)).fetchone()
    )
    restarted = create_app(dict(app.config))
    web = restarted.test_client()
    with web.session_transaction() as sess:
        sess.update(client_id=1, client_session_version=1)
    assert web.get("/cliente/plano").status_code == 200
    assert web.get("/cliente/plano/pagamentos/" + order["id"]).status_code == 200
    after = dict(
        db.execute("SELECT * FROM plan_orders WHERE id=?", (order["id"],)).fetchone()
    )
    assert (
        before["rules_text"] == after["rules_text"]
        and before["paid_at"] == after["paid_at"]
    )
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1
    assert db.execute("SELECT COUNT(*) n FROM plan_credit_ledger").fetchone()["n"] == 1


def test_webhook_malformed_and_size_limit_without_csrf_bypass_elsewhere(site):
    _, client, _, _ = site
    raw = b"[]"
    sig = hmac.new(
        CONFIG["FASTFLOW_WEBHOOK_SECRET"].encode(), raw, hashlib.sha256
    ).hexdigest()
    assert (
        client.post(
            "/webhooks/fastflow",
            data=raw,
            headers={"X-Webhook-Signature": "sha256=" + sig},
        ).status_code
        == 400
    )
    assert client.post("/webhooks/fastflow", data=b"x" * 65537).status_code == 413
    assert (
        client.post("/cliente/plano/assinar", data={"method": "pix"}).status_code == 400
    )
