"""Four updates: owner-scoped tabs and isolated, mocked payment purposes."""

import json, hmac, hashlib, uuid, re
from datetime import datetime, timedelta
import pytest
import requests
from test_plan import site, CONFIG, post_purchase
from test_app import token, signature
from core import TZ
from fastflow_gateway import Settings, GatewayError, FastFlow
import plan_service as ps
import pickup_payments as pp


@pytest.fixture
def payments(site, monkeypatch):
    app, client, db, fake = site
    db.execute(
        "UPDATE settings SET pix_receiver_name='Responsavel Teste',pix_key_type='CNPJ',pix_key='38145273000105'"
    )
    db.commit()
    original = fake.create

    def create(order, owner, card=None):
        try:
            data = original(order, owner, card)
        finally:
            tid = str(fake.creates)
            if tid in fake.records:
                fake.records[tid]["amount"] = order["amount_cents"] / 100
        return fake.records[data["id"]].copy()

    monkeypatch.setattr(fake, "create", create)
    monkeypatch.setattr(
        fake,
        "cancel",
        lambda tid: fake.records[str(tid)].update(status="cancelled"),
        raising=False,
    )
    return site


def staff(client):
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True)


def package(db, number=1, base=4.9, client_id=1):
    now = datetime.now(TZ).isoformat()
    result = db.execute(
        "INSERT INTO packages(client_id,public_id,tracking,width,height,length,weight,shelf,category,base_price,status,created_at,notified_at) VALUES(?,?,?,10,10,10,1,'A1','Pequeno',?,'aguardando_retirada',?,?) RETURNING id",
        (client_id, f"PCTPCA-{number}", f"PKG{number}", base, now, now),
    ).fetchone()["id"]
    db.commit()
    return result


def create_pickup(client, ids, **extra):
    return client.post(
        "/painel/retirada/pix-fast",
        data={
            "package_ids": [str(i) for i in ids],
            "csrf_token": token(client),
            **extra,
        },
    )


def deliver(client, ids, method="Pix Fast", **extra):
    return client.post(
        "/retiradas",
        data={
            "package_ids": [str(i) for i in ids],
            "receiver_name": "Pessoa Ficticia",
            "receiver_document": "52998224725",
            "document_type": "CPF",
            "pickup_signature": signature(),
            "payment_method": method,
            "payment_confirmed": "yes",
            "csrf_token": token(client),
            **extra,
        },
    )


def signed(client, path, tid, status="paid"):
    raw = json.dumps({"transaction_id": str(tid), "status": status}).encode()
    sig = hmac.new(
        CONFIG["FASTFLOW_WEBHOOK_SECRET"].encode(), raw, hashlib.sha256
    ).hexdigest()
    return client.post(
        path,
        data=raw,
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Signature": "sha256=" + sig,
        },
    )


def admin_form(client, path, method):
    response = client.get(path)
    assert response.status_code == 200
    data = {
        n: re.search('name="' + n + '" value="([^"]*)"', response.text).group(1)
        for n in ("operation_id", "cycle_key")
    }
    return {
        **data,
        "csrf_token": token(client),
        "reason": "Conferencia de pagamento ficticio no ponto.",
        "confirm": "yes",
        "payment_confirmed": "yes",
        "payment_method": method,
    }


def test_six_tabs_owned_counts_and_password_rotation(payments):
    app, client, db, _ = payments
    package(db)
    for tab in ("visao-geral", "pacotes", "plano", "historico", "dados", "contratos"):
        page = client.get("/cliente?aba=" + tab)
        assert page.status_code == 200 and "Menu da área" in page.text
    assert client.get("/cliente?aba=invalid").status_code == 404
    other = app.test_client()
    with client.session_transaction() as s:
        with other.session_transaction() as old:
            old.update(dict(s))
    response = client.post(
        "/cliente/alterar-senha",
        data={
            "current_password": "senha-segura",
            "password": "nova-senha-123",
            "password_confirm": "nova-senha-123",
            "csrf_token": token(client),
        },
    )
    assert (
        response.status_code == 302
        and client.get("/cliente?aba=dados").status_code == 200
    )
    assert other.get("/cliente").status_code == 302


@pytest.mark.parametrize("method", ["Pix AI", "Dinheiro"])
def test_manual_pickup_does_not_call_api_and_records_names(
    payments, monkeypatch, method
):
    _, client, db, fake = payments
    pid = package(db)
    staff(client)
    monkeypatch.setattr(
        "plan_routes.gateway", lambda *a: pytest.fail("No API for manual payment")
    )
    response = deliver(client, [pid], method)
    assert response.status_code == 302
    row = db.execute("SELECT * FROM pickups").fetchone()
    assert row["payment_method"] == method and row["payment_status"] == "paid_manual"
    assert db.execute("SELECT status FROM packages").fetchone()["status"] == "retirado"


def test_quote_surcharge_before_discount_and_zero_fast_no_charge(payments):
    _, client, db, fake = payments
    pid = package(db)
    staff(client)
    summary = client.post(
        "/painel/retirada/resumo",
        data={
            "package_ids": pid,
            "surcharge_amount": "3",
            "surcharge_reason": "Servico",
            "discount_percent": "25",
            "csrf_token": token(client),
        },
    )
    assert summary.json["original_total"] == 7.9 and summary.json["total"] == 5.92
    assert (
        create_pickup(client, [pid], discount_percent="100").status_code == 400
        and fake.creates == 0
    )
    assert deliver(client, [pid], "Pix Fast", discount_percent="100").status_code == 302
    assert db.execute("SELECT total_paid FROM pickups").fetchone()["total_paid"] == 0


def test_fast_payment_then_separate_physical_delivery_and_duplicate_guard(payments):
    _, client, db, fake = payments
    pid = package(db)
    staff(client)
    created = create_pickup(client, [pid])
    assert created.status_code == 200
    oid = created.json["id"]
    assert create_pickup(client, [pid]).json["id"] == oid and fake.creates == 1
    order = db.execute("SELECT * FROM pickup_payment_orders").fetchone()
    tid = order["transaction_id"]
    assert order["amount_cents"] == 490 and fake.records[tid]["amount"] == 4.9
    assert deliver(client, [pid], fast_order_id=oid).status_code == 302
    assert not db.execute("SELECT 1 FROM pickups").fetchone()
    fake.records[tid]["status"] = "approved"
    assert (
        signed(
            client, "/webhooks/fastflow/retiradas/" + oid, tid, status="approved"
        ).status_code
        == 200
    )
    assert (
        db.execute("SELECT status FROM packages").fetchone()["status"]
        == "aguardando_retirada"
    )
    fake.records[tid]["status"] = "paid"
    for _ in range(2):
        assert (
            signed(client, "/webhooks/fastflow/retiradas/" + oid, tid).status_code
            == 200
        )
    assert (
        not db.execute("SELECT 1 FROM pickups").fetchone()
        and not db.execute("SELECT 1 FROM plan_cycles").fetchone()
    )
    assert deliver(client, [pid], fast_order_id=oid).status_code == 302
    assert db.execute("SELECT COUNT(*) n FROM pickups").fetchone()["n"] == 1
    assert (
        db.execute("SELECT payment_method FROM pickups").fetchone()["payment_method"]
        == "Pix Fast"
    )
    deliver(client, [pid], fast_order_id=oid)
    assert db.execute("SELECT COUNT(*) n FROM pickups").fetchone()["n"] == 1


def test_locked_packages_reject_manual_mode_or_changed_value(payments):
    _, client, db, fake = payments
    pid = package(db)
    staff(client)
    oid = create_pickup(client, [pid]).json["id"]
    assert (
        create_pickup(
            client, [pid], surcharge_amount="1", surcharge_reason="Extra"
        ).status_code
        == 400
    )
    deliver(client, [pid], "Dinheiro")
    assert not db.execute("SELECT 1 FROM pickups").fetchone() and fake.creates == 1
    fake.records["1"]["status"] = "paid"
    signed(client, "/webhooks/fastflow/retiradas/" + oid, 1)
    deliver(client, [pid], fast_order_id=oid, discount_percent="10")
    assert not db.execute("SELECT 1 FROM pickups").fetchone()


@pytest.mark.parametrize(
    "patch",
    [
        {"amount": 4.91},
        {"payment_provider": "other"},
        {"notification_url": "https://example.test/wrong"},
        {"user": {"cpf_cnpj": "11111111111"}},
    ],
)
def test_pickup_webhook_mismatch_cannot_confirm(payments, patch):
    _, client, db, fake = payments
    pid = package(db)
    staff(client)
    oid = create_pickup(client, [pid]).json["id"]
    fake.records["1"].update(status="paid", **patch)
    assert signed(client, "/webhooks/fastflow/retiradas/" + oid, 1).status_code == 202
    assert (
        db.execute("SELECT paid_at FROM pickup_payment_orders").fetchone()["paid_at"]
        is None
    )
    assert not db.execute("SELECT 1 FROM pickups").fetchone()


def test_payment_purpose_separation_and_unsigned_webhook(payments):
    _, client, db, fake = payments
    plan = post_purchase(client)
    plan_order = db.execute("SELECT * FROM plan_orders").fetchone()
    pid = package(db)
    staff(client)
    oid = create_pickup(client, [pid]).json["id"]
    assert (
        client.post(
            "/webhooks/fastflow/retiradas/" + oid,
            json={"transaction_id": "2", "status": "paid"},
        ).status_code
        == 401
    )
    fake.records["1"]["status"] = "paid"
    assert signed(client, "/webhooks/fastflow/retiradas/" + oid, 1).status_code in (
        202,
        409,
    )
    assert signed(
        client, "/webhooks/fastflow/pedidos/" + plan_order["id"], 2
    ).status_code in (202, 409)
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone()
    assert (
        db.execute("SELECT paid_at FROM pickup_payment_orders").fetchone()["paid_at"]
        is None
    )


def test_pickup_timeout_is_reconciled_without_repost(payments):
    app, client, db, fake = payments
    pid = package(db)
    staff(client)
    fake.failure = GatewayError(uncertain=True)
    oid = create_pickup(client, [pid]).json["id"]
    assert fake.creates == 1
    create_pickup(client, [pid])
    assert fake.creates == 1
    fake.failure = None
    fake.records["1"]["status"] = "paid"
    pp.reconcile(db, app, datetime.now(TZ) + timedelta(minutes=3))
    assert (
        db.execute("SELECT state FROM pickup_payment_orders").fetchone()["state"]
        == "paid"
    )
    assert not db.execute("SELECT 1 FROM pickups").fetchone()


@pytest.mark.parametrize("method", ["Pix AI", "Dinheiro"])
def test_manual_plan_exact_methods_receipt_and_no_api(payments, monkeypatch, method):
    _, client, db, fake = payments
    staff(client)
    path = "/painel/configuracoes/plano/clientes/1/validar"
    form = admin_form(client, path, method)
    monkeypatch.setattr(
        "plan_routes.gateway", lambda *a: pytest.fail("No API for manual plan")
    )
    response = client.post(path, data=form, follow_redirects=True)
    assert response.status_code == 200 and "Plano ativado com sucesso" in response.text
    row = db.execute("SELECT * FROM plan_orders").fetchone()
    assert (
        row["local_method"] == method
        and row["manual_paid_at"]
        and row["paid_at"] is None
        and row["accepted_at"] == ""
    )
    assert (
        db.execute("SELECT payment_method FROM plan_admin_decisions").fetchone()[
            "payment_method"
        ]
        == method
    )
    assert client.get(
        "/painel/configuracoes/plano/pagamentos/" + row["id"] + "/comprovante.pdf"
    ).data.startswith(b"%PDF")


def test_admin_fast_no_manual_override_and_automatic_activation(payments):
    _, client, db, fake = payments
    staff(client)
    path = "/painel/configuracoes/plano/clientes/1/validar"
    form = admin_form(client, path, "Pix Fast")
    response = client.post(path, data=form)
    assert response.status_code == 302 and "/pagamentos/" in response.location
    order = db.execute("SELECT * FROM plan_orders").fetchone()
    assert (
        order["accepted_at"] == ""
        and not db.execute("SELECT 1 FROM plan_cycles").fetchone()
    )
    client.post(path, data=form)
    assert fake.creates == 1
    direct = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/approve"
    assert (
        client.post(direct, data=admin_form(client, direct, "Dinheiro")).status_code
        == 400
    )
    fake.records["1"]["status"] = "paid"
    assert (
        signed(client, "/webhooks/fastflow/pedidos/" + order["id"], 1).status_code
        == 200
    )
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1
    assert "Plano ativado com sucesso" in client.get(response.location).text


def test_cancellation_403_is_pending_with_sanitized_diagnostics(payments, monkeypatch):
    _, client, db, fake = payments
    post_purchase(client)
    order = db.execute("SELECT * FROM plan_orders").fetchone()
    staff(client)

    def reject(*a):
        raise GatewayError(
            code="rejected",
            http_status=403,
            http_method="PUT",
            http_endpoint="/transactions/1",
        )

    monkeypatch.setattr(fake, "cancel", reject)
    path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/cancel"
    response = client.post(
        path, data=admin_form(client, path, "Dinheiro"), follow_redirects=True
    )
    assert "Cancelamento aguardando verificação" in response.text
    row = db.execute("SELECT * FROM plan_orders").fetchone()
    assert row["state"] == "cancel_requested" and row["paid_at"] is None
    decision = db.execute("SELECT * FROM plan_admin_decisions").fetchone()
    assert (
        decision["http_status"] == 403
        and decision["http_method"] == "PUT"
        and decision["result"] == "awaiting_verification"
    )
    fake.records["1"]["status"] = "paid"
    signed(client, "/webhooks/fastflow/pedidos/" + order["id"], 1)
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone()
    assert (
        db.execute("SELECT error_code FROM plan_orders").fetchone()["error_code"]
        == "payment_after_cancellation"
    )


def test_cancel_documented_put_and_http_metadata(monkeypatch):
    calls = []

    class Reply:
        status_code = 403

        def json(self):
            return {"message": "secret must never be copied"}

    monkeypatch.setattr(
        requests,
        "request",
        lambda method, url, **kw: (calls.append((method, url, kw)) or Reply()),
    )
    with pytest.raises(GatewayError) as result:
        FastFlow(Settings.from_config(CONFIG)).cancel("12")
    assert calls[0][0] == "PUT" and calls[0][2]["json"] == {"status": "cancelled"}
    assert (
        result.value.http_status == 403
        and result.value.http_endpoint == "/transactions/12"
    )
    assert "secret" not in str(result.value)


@pytest.mark.parametrize("base", [3.99, 1000.01])
def test_fast_limits_offer_manual_without_changing_value(payments, base):
    _, client, db, fake = payments
    pid = package(db, base=base)
    staff(client)
    response = create_pickup(client, [pid])
    assert (
        response.status_code == 400 and "Pix AI ou Dinheiro" in response.json["error"]
    )
    assert (
        fake.creates == 0
        and not db.execute("SELECT 1 FROM pickup_payment_orders").fetchone()
    )


def test_late_pickup_payment_keeps_history_and_never_delivers(payments):
    app, client, db, fake = payments
    pid = package(db)
    staff(client)
    oid = create_pickup(client, [pid]).json["id"]
    fake.records["1"]["status"] = "expired"
    pp.apply(db, oid, fake.records["1"], datetime.now(TZ))
    assert not db.execute("SELECT 1 FROM pickup_payment_reservations").fetchone()
    fake.records["1"]["status"] = "paid"
    signed(client, "/webhooks/fastflow/retiradas/" + oid, 1)
    assert (
        db.execute("SELECT error_code FROM pickup_payment_orders").fetchone()[
            "error_code"
        ]
        == "late_payment_review"
    )
    deliver(client, [pid], fast_order_id=oid)
    assert not db.execute("SELECT 1 FROM pickups").fetchone()
    assert (
        db.execute("SELECT status FROM packages").fetchone()["status"]
        == "aguardando_retirada"
    )


def test_owner_scope_on_new_tabs_and_receipts(payments):
    app, client, db, fake = payments
    post_purchase(client)
    oid = db.execute("SELECT id FROM plan_orders").fetchone()["id"]
    clone = dict(db.execute("SELECT * FROM clients WHERE id=1").fetchone())
    clone.pop("id")
    clone.update(
        name="Outro Cliente Ficticio",
        cpf="39053344705",
        email="outro@example.test",
        public_id="CLIPCA-2",
    )
    cid = db.execute(
        "INSERT INTO clients("
        + ",".join(clone)
        + ") VALUES("
        + ",".join("?" for _ in clone)
        + ") RETURNING id",
        tuple(clone.values()),
    ).fetchone()["id"]
    db.commit()
    package(db, number=9, client_id=cid)
    assert "PCTPCA-9" not in client.get("/cliente?aba=pacotes").text
    with client.session_transaction() as s:
        s.clear()
        s.update(client_id=cid, client_session_version=1)
    assert (
        client.get("/cliente/plano/pagamentos/" + oid + "/comprovante.pdf").status_code
        == 404
    )
    assert client.get("/painel/retirada/pix-fast/nonexistent").status_code == 302


def test_concurrent_pickup_generation_and_delivery(payments):
    from concurrent.futures import ThreadPoolExecutor

    app, _, db, fake = payments
    pid = package(db)

    def generate(_):
        client = app.test_client()
        staff(client)
        return create_pickup(client, [pid]).json["id"]

    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(generate, range(3)))
    assert len(set(ids)) == 1 and fake.creates == 1
    fake.records["1"]["status"] = "paid"
    pp.apply(db, ids[0], fake.records["1"], datetime.now(TZ))

    def handoff(_):
        client = app.test_client()
        staff(client)
        return deliver(client, [pid], fast_order_id=ids[0]).status_code

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(handoff, range(3)))
    assert db.execute("SELECT COUNT(*) n FROM pickups").fetchone()["n"] == 1
    assert db.execute("SELECT COUNT(*) n FROM pickup_packages").fetchone()["n"] == 1


def test_no_client_or_staff_without_permission_can_generate(payments):
    _, client, db, fake = payments
    pid = package(db)
    assert create_pickup(client, [pid]).status_code == 302
    sid = db.execute(
        "INSERT INTO staff_users(name,email,password_hash,role,can_pickups,created_at) VALUES('Sem retirada','no-pickup@example.test','fixture','atendente',0,?) RETURNING id",
        (datetime.now(TZ).isoformat(),),
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True, staff_user_id=sid, staff_session_version=1)
    assert create_pickup(client, [pid]).status_code == 403 and fake.creates == 0


@pytest.mark.skipif(
    not __import__("os").environ.get("TEST_DATABASE_URL"),
    reason="PostgreSQL isolado não configurado",
)
def test_postgres_pickup_reservations_and_physical_delivery(monkeypatch):
    import os
    from concurrent.futures import ThreadPoolExecutor
    from app import create_app
    from database import DatabaseConnection
    from test_plan import FakeFlow
    import plan_routes

    path = os.environ["TEST_DATABASE_URL"]
    app = create_app({**CONFIG, "DATABASE": path})
    db = DatabaseConnection(path)
    now = datetime.now(TZ).isoformat()
    cid = db.execute(
        "INSERT INTO clients(name,cpf,phone,terms_version,terms_text,signature,accepted_at) VALUES(?,'12345678909','16999999999','pickup-fixture-v1','fixture','fixture',?) RETURNING id",
        ("Pickup fixture " + str(uuid.uuid4()), now),
    ).fetchone()["id"]
    db.commit()
    pid = db.execute(
        "INSERT INTO packages(client_id,public_id,tracking,width,height,length,weight,shelf,category,base_price,status,created_at,notified_at) VALUES(?,?,?,10,10,10,1,'A1','Pequeno',4.9,'aguardando_retirada',?,?) RETURNING id",
        (cid, "PG-" + str(uuid.uuid4()), "PG" + uuid.uuid4().hex, now, now),
    ).fetchone()["id"]
    db.commit()
    fake = FakeFlow()
    original = fake.create

    def create(order, owner, card=None):
        data = original(order, owner, card)
        fake.records[data["id"]]["amount"] = order["amount_cents"] / 100
        return fake.records[data["id"]].copy()

    monkeypatch.setattr(fake, "create", create)
    monkeypatch.setattr(plan_routes, "gateway", lambda *a: fake)

    def generate(_):
        web = app.test_client()
        staff(web)
        result = create_pickup(web, [pid])
        assert result.status_code == 200
        return result.json["id"]

    with ThreadPoolExecutor(max_workers=3) as pool:
        oids = list(pool.map(generate, range(3)))
    assert len(set(oids)) == 1 and fake.creates == 1
    fake.records["1"]["status"] = "paid"
    pp.apply(db, oids[0], fake.records["1"], datetime.now(TZ))

    def receive(_):
        web = app.test_client()
        staff(web)
        return deliver(web, [pid], fast_order_id=oids[0]).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(receive, range(2)))
    assert (
        db.execute(
            "SELECT COUNT(*) n FROM pickup_packages WHERE package_id=?", (pid,)
        ).fetchone()["n"]
        == 1
    )
    assert (
        db.execute(
            "SELECT state FROM pickup_payment_orders WHERE id=?", (oids[0],)
        ).fetchone()["state"]
        == "delivered"
    )
    db.close()


def test_cancellation_rechecks_creation_marker_under_lock(payments, monkeypatch):
    _, client, db, fake = payments
    owner = db.execute("SELECT * FROM clients WHERE id=1").fetchone()
    value = datetime.now(TZ)
    order, _ = ps.reserve_order(db, owner, "pix", value, Settings.from_config(CONFIG))
    assert order["creation_started_at"] == ""
    staff(client)
    path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/cancel"
    form = admin_form(client, path, "Dinheiro")
    original = ps.lock_account

    def creation_started(connection, client_id, current):
        original(connection, client_id, current)
        connection.execute(
            "UPDATE plan_orders SET creation_started_at=? WHERE id=?",
            (ps.iso(value), order["id"]),
        )

    monkeypatch.setattr(ps, "lock_account", creation_started)
    response = client.post(path, data=form, follow_redirects=True)
    assert response.status_code == 200
    assert "Cancelamento aguardando verificação" in response.text
    assert (
        db.execute(
            "SELECT state FROM plan_orders WHERE id=?", (order["id"],)
        ).fetchone()["state"]
        == "cancel_requested"
    )
    assert fake.creates == 0
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone()


def test_pickup_cancel_validates_binding_before_remote_mutation(payments, monkeypatch):
    _, client, db, fake = payments
    pid = package(db)
    staff(client)
    assert create_pickup(client, [pid]).status_code == 200
    order = db.execute("SELECT * FROM pickup_payment_orders").fetchone()
    fake.records[order["transaction_id"]][
        "notification_url"
    ] = "https://wrong.example.test/other-order"
    calls = []
    monkeypatch.setattr(fake, "cancel", lambda tid: calls.append(tid))
    response = client.post(
        "/painel/retirada/pix-fast/" + order["id"] + "/cancelar",
        data={"csrf_token": token(client)},
    )
    assert response.status_code == 302
    assert calls == []
    assert (
        db.execute("SELECT state FROM pickup_payment_orders").fetchone()["state"]
        == "cancel_requested"
    )
    assert (
        db.execute("SELECT status FROM packages WHERE id=?", (pid,)).fetchone()[
            "status"
        ]
        == "aguardando_retirada"
    )


@pytest.mark.parametrize(
    "total, adjustments, allowed",
    [
        (3.99, {"discount_amount": "0.91"}, False),
        (4.00, {"discount_amount": "0.90"}, True),
        (
            1000.00,
            {"surcharge_amount": "995.10", "surcharge_reason": "Ajuste ficticio"},
            True,
        ),
        (
            1000.01,
            {"surcharge_amount": "995.11", "surcharge_reason": "Ajuste ficticio"},
            False,
        ),
    ],
)
def test_pickup_fast_final_amount_boundaries(payments, total, adjustments, allowed):
    _, client, db, fake = payments
    pid = package(db)
    staff(client)
    summary = client.post(
        "/painel/retirada/resumo",
        data={"package_ids": pid, "csrf_token": token(client), **adjustments},
    )
    assert summary.json["total"] == total
    assert summary.json["fast_available"] is allowed
    assert summary.json["fast_range_message"] == pp.FAST_RANGE_MESSAGE
    response = create_pickup(client, [pid], **adjustments)
    assert response.status_code == (200 if allowed else 400)
    assert fake.creates == int(allowed)
    if allowed:
        row = db.execute("SELECT * FROM pickup_payment_orders").fetchone()
        assert row["amount_cents"] == round(total * 100)
        assert fake.records[row["transaction_id"]]["amount"] == total
    else:
        assert response.json["error"] == pp.FAST_RANGE_MESSAGE
        assert not db.execute("SELECT 1 FROM pickup_payment_orders").fetchone()


def preview(client, **overrides):
    return client.post(
        "/painel/pacotes/cobertura",
        data={
            "client_id": 1,
            "width": "10",
            "height": "10",
            "length": "10",
            "weight": "1",
            "csrf_token": token(client),
            **overrides,
        },
    )


def test_preview_readonly_uses_category_and_balance(payments):
    _, client, db, fake = payments
    fake.state = "paid"
    post_purchase(client)
    db.execute("UPDATE plan_cycles SET used_credits=4")
    db.commit()
    staff(client)
    before = db.execute("SELECT COUNT(*) n FROM plan_credit_ledger").fetchone()["n"]
    small = preview(client).json
    assert (
        small["message"]
        == "Plano ativo · 6 créditos disponíveis · Este pacote usará 1 crédito."
    )
    large = preview(client, width="40", height="40", length="40", weight="12").json
    assert large["category"] == "Grande" and large["credits"] == 2
    assert (
        db.execute("SELECT used_credits FROM plan_cycles").fetchone()["used_credits"]
        == 4
    )
    assert (
        db.execute("SELECT COUNT(*) n FROM plan_credit_ledger").fetchone()["n"]
        == before
    )
    db.execute("UPDATE plan_cycles SET used_credits=9")
    db.commit()
    insufficient = preview(
        client, width="40", height="40", length="40", weight="12"
    ).json
    assert (
        insufficient["message"] == "Recebimento avulso · R$ 9,90."
        and not insufficient["covered"]
    )
    assert preview(client, width="NaN").status_code == 400
    assert preview(client, client_id=999).status_code == 400


def test_receipt_coverage_persists_after_expiry_and_not_retroactive(payments):
    _, client, db, fake = payments
    old = package(db)
    staff(client)
    assert preview(client).json["message"] == "Recebimento avulso · R$ 4,90."
    with client.session_transaction() as session:
        session.clear()
        session.update(client_id=1, client_session_version=1)
    fake.state = "paid"
    post_purchase(client)
    staff(client)
    response = client.post(
        "/pacotes",
        data={
            "client_id": 1,
            "tracking": "COVERNEW",
            "shelf": "A2",
            "width": 10,
            "height": 10,
            "length": 10,
            "weight": 1,
            "csrf_token": token(client),
        },
    )
    assert response.status_code == 302
    covered = db.execute("SELECT * FROM packages WHERE tracking='COVERNEW'").fetchone()
    assert covered["base_price"] == 0 and ps.coverage(db, covered["id"])["amount"] == -1
    assert ps.coverage(db, old) is None
    before = db.execute(
        "SELECT COUNT(*) n FROM plan_credit_ledger WHERE kind='consume'"
    ).fetchone()["n"]
    db.execute(
        "UPDATE plan_cycles SET ends_at=?",
        (ps.iso(datetime.now(TZ) - timedelta(days=1)),),
    )
    db.execute(
        "UPDATE packages SET status='aguardando_retirada',notified_at=?",
        (ps.iso(datetime.now(TZ) - timedelta(days=5)),),
    )
    db.commit()
    assert not preview(client).json["covered"]
    summary = client.post(
        "/painel/retirada/resumo",
        data={"package_ids": [old, covered["id"]], "csrf_token": token(client)},
    ).json
    messages = {p["id"]: p for p in summary["package_coverage"]}
    assert (
        messages[covered["id"]]["message"]
        == "Incluído no plano · 1 crédito já utilizado · Recebimento: R$ 0,00."
    )
    assert messages[old]["message"] == "Avulso · Recebimento: R$ 4,90."
    assert messages[old]["late"] > 0 and messages[covered["id"]]["late"] > 0
    assert "Incluído no plano" in client.get("/painel").text
    assert deliver(client, [old, covered["id"]], "Dinheiro").status_code == 302
    assert (
        db.execute(
            "SELECT COUNT(*) n FROM plan_credit_ledger WHERE kind='consume'"
        ).fetchone()["n"]
        == before
    )
    assert ps.coverage(db, covered["id"]) is not None and ps.coverage(db, old) is None


def test_coverage_preview_requires_staff(payments):
    _, client, _, _ = payments
    assert preview(client).status_code == 302
