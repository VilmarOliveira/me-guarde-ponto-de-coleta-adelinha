"""Administrative plan grants and unpaid cancellations; no real payments/email."""

import re, uuid
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from test_plan import site, CONFIG, START, reserve, create_cycle, person, insert_package
from test_app import token
from database import DatabaseConnection
from fastflow_gateway import Settings, GatewayError
from core import TZ
import plan_service as ps
import plan_administration as admin_service
from plan_jobs import reconcile, reminders

ACTOR = {"id": None, "key": "legacy", "name": "Administrador fixture"}
REASON = "Conferência e autorização administrativa do cliente no ponto."


def staff(client):
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True)


def fields(client, path):
    response = client.get(path)
    assert response.status_code == 200
    result = {
        name: re.search('name="' + name + '" value="([^"]*)"', response.text).group(1)
        for name in ("operation_id", "cycle_key")
    }
    return {**result,"csrf_token":token(client),"reason":REASON,"confirm":"yes","payment_method":"Dinheiro","payment_confirmed":"yes"}


def approve_direct(db, value=START, operation=None, order=None, expected=None):
    return admin_service.approve(
        db,
        1,
        order,
        ACTOR,
        REASON,
        operation or str(uuid.uuid4()),
        value,
        Settings.from_config(CONFIG),
        expected_key=expected or ps.next_cycle_key(db, 1, value),payment_method="Dinheiro",payment_confirmed=True,
    )


def test_admin_enrolls_registered_client_without_charge_or_false_acceptance(site):
    app, client, db, fake = site
    staff(client)
    path = "/painel/configuracoes/plano/clientes/1/validar"
    form = fields(client, path)
    response = client.post(path, data=form, follow_redirects=True)
    assert (
        response.status_code == 200
        and "Plano ativado com sucesso" in response.text
    )
    order = db.execute("SELECT * FROM plan_orders").fetchone()
    assert order["state"] == "manual_approved" and order["method"] == "administrative"
    assert (
        order["accepted_at"] == ""
        and order["paid_at"] is None
        and order["transaction_id"] is None
    )
    assert fake.creates == 0 and fake.gets == 0
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1
    assert (
        db.execute("SELECT amount FROM plan_credit_ledger").fetchone()["amount"] == 10
    )
    decision = db.execute("SELECT * FROM plan_admin_decisions").fetchone()
    assert decision["actor_name"] == app.config["STAFF_NAME"]
    assert decision["reason"] == REASON and decision["action"] == "approve"
    with client.session_transaction() as s:
        s.clear()
        s.update(client_id=1, client_session_version=1)
    page = client.get("/cliente/plano/pagamentos/" + order["id"])
    assert (
        page.status_code == 200
        and "validada manualmente" in page.text
        and "conferido manualmente" in page.text
    )
    assert "Pagamento aprovado!" not in page.text


def test_double_submit_and_concurrent_approval_grant_only_once(site):
    app, client, db, _ = site
    staff(client)
    path = "/painel/configuracoes/plano/clientes/1/validar"
    form = fields(client, path)
    for _ in range(2):
        assert client.post(path, data=form).status_code == 302
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1
    assert (
        db.execute("SELECT COUNT(*) n FROM plan_admin_decisions").fetchone()["n"] == 1
    )


def test_same_preview_different_operations_cannot_accidentally_renew_twice(site):
    _, _, db, _ = site
    key = ps.next_cycle_key(db, 1, START)
    approve_direct(db, expected=key)
    with pytest.raises(ValueError, match="ciclo mudou"):
        approve_direct(db, expected=key)
    db.rollback()
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1


def test_manual_renewal_respects_next_start_and_suppresses_reminders(site):
    app, _, db, fake = site
    create_cycle(db, fake)
    day = datetime(2026, 2, 27, 8, tzinfo=TZ)
    oid = approve_direct(db, day)
    current, future, _ = ps.cycles(db, 1, day)
    assert future["order_id"] == oid and future["granted_at"] is None
    assert ps.parse(future["starts_at"]) == ps.parse(current["ends_at"])
    assert (
        db.execute(
            "SELECT COUNT(*) n FROM plan_credit_ledger WHERE kind='grant'"
        ).fetchone()["n"]
        == 1
    )
    assert (
        reminders(
            db,
            app.config,
            day,
            lambda *a, **k: pytest.fail("No reminder after manual renewal"),
        )
        == 0
    )
    assert (
        reminders(
            db,
            app.config,
            day + timedelta(days=1),
            lambda *a, **k: pytest.fail("No reminder after manual renewal"),
        )
        == 0
    )


def test_manual_approval_cannot_override_fast_and_late_duplicate_is_flagged(site):
    _,_,db,fake=site;order,data=create_cycle(db,fake,state='pending')
    with pytest.raises(ValueError,match='Pix Fast'):approve_direct(db,order=order['id'])
    db.rollback();data['status']='expired';ps.apply_transaction(db,order['id'],data,START)
    approve_direct(db,order=order['id'])
    data['status']='paid';ps.apply_transaction(db,order['id'],data,START+timedelta(minutes=3))
    row=db.execute('SELECT * FROM plan_orders').fetchone()
    assert row['state']=='manual_approved' and row['error_code']=='duplicate_manual_and_fast_payment'
    assert db.execute('SELECT COUNT(*) n FROM plan_cycles').fetchone()['n']==1
    assert db.execute('SELECT COUNT(*) n FROM plan_credit_ledger').fetchone()['n']==1


@pytest.mark.parametrize(
    "change",
    [
        "active=0",
        "signature=''",
        "confirmation_validated_at=NULL",
        "residential_street=''",
    ],
)
def test_manual_validation_cannot_forge_or_skip_existing_requirements(site, change):
    _, client, db, fake = site
    db.execute("UPDATE clients SET " + change)
    db.commit()
    staff(client)
    path = "/painel/configuracoes/plano/clientes/1/validar"
    form = fields(client, path)
    assert client.post(path, data=form).status_code == 400
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone() and fake.creates == 0


def test_existing_paid_or_admin_approved_participation_cannot_be_cancelled(site):
    app, client, db, fake = site
    order, data = create_cycle(db, fake)
    staff(client)
    path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/cancel"
    form = fields(client, path)
    assert client.post(path, data=form).status_code == 400
    assert db.execute("SELECT state FROM plan_orders").fetchone()["state"] == "paid"
    assert not db.execute(
        "SELECT 1 FROM plan_admin_decisions WHERE action='cancel'"
    ).fetchone()


def test_cancel_pending_with_provider_confirmation_no_new_credits_or_deletion(
    site, monkeypatch
):
    _, client, db, fake = site
    order, data = create_cycle(db, fake, state="pending")
    staff(client)
    cancellations = []

    def cancel(tid):
        cancellations.append(tid)
        fake.records[tid]["status"] = "cancelled"
        return fake.records[tid]

    monkeypatch.setattr(fake, "cancel", cancel, raising=False)
    path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/cancel"
    form = fields(client, path)
    response = client.post(path, data=form, follow_redirects=True)
    assert (
        response.status_code == 200
        and "Solicitação não paga cancelada" in response.text
    )
    assert cancellations == [data["id"]]
    saved = db.execute("SELECT * FROM plan_orders").fetchone()
    assert saved["state"] == "admin_cancelled" and saved["qr_text"] is None
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone()
    assert client.post(path, data=form).status_code == 302 and len(cancellations) == 1
    assert (
        db.execute("SELECT COUNT(*) n FROM plan_admin_decisions").fetchone()["n"] == 1
    )
    assert db.execute("SELECT COUNT(*) n FROM plan_orders").fetchone()["n"] == 1


def test_cancel_detects_payment_and_does_not_call_delete(site, monkeypatch):
    _, client, db, fake = site
    order, data = create_cycle(db, fake, state="pending")
    staff(client)
    fake.records[data["id"]]["status"] = "paid"
    monkeypatch.setattr(
        fake,
        "cancel",
        lambda *a: pytest.fail("Paid charge must not be cancelled"),
        raising=False,
    )
    path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/cancel"
    assert client.post(path, data=fields(client, path)).status_code == 400
    assert db.execute("SELECT state FROM plan_orders").fetchone()["state"] == "paid"
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1


def test_cancel_api_failure_keeps_existing_request(site, monkeypatch):
    _, client, db, fake = site
    order, data = create_cycle(db, fake, state="pending")
    staff(client)

    def fail(*a):
        raise GatewayError(uncertain=True)

    monkeypatch.setattr(fake, "cancel", fail, raising=False)
    path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/cancel"
    assert client.post(path, data=fields(client, path)).status_code == 302
    assert db.execute("SELECT state FROM plan_orders").fetchone()["state"] == "pending"
    assert db.execute("SELECT result FROM plan_admin_decisions").fetchone()["result"]=="awaiting_verification"


def test_late_paid_after_admin_cancel_does_not_activate_or_erase_history(site):
    app, _, db, fake = site
    order, data = create_cycle(db, fake, state="expired")
    admin_service.cancel(
        db,
        app,
        order["id"],
        ACTOR,
        REASON,
        str(uuid.uuid4()),
        START,
        Settings.from_config(CONFIG),
    )
    for status in ("paid", "pending", "paid"):
        data["status"] = status
        ps.apply_transaction(db, order["id"], data, START + timedelta(days=1))
    row = db.execute("SELECT * FROM plan_orders").fetchone()
    assert (
        row["state"] == "paid"
        and row["paid_at"]
        and row["error_code"] == "payment_after_cancellation"
    )
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone()
    assert (
        db.execute("SELECT COUNT(*) n FROM plan_admin_decisions").fetchone()["n"] == 1
    )


def test_customer_manager_and_attendant_cannot_access_admin_routes(site):
    _, client, db, _ = site
    routes = [
        "/painel/configuracoes/plano/clientes/1/validar",
        "/painel/configuracoes/plano/pedidos/none/approve",
        "/painel/configuracoes/plano/pedidos/none/cancel",
    ]
    for path in routes:
        assert client.get(path).status_code == 302
        assert (
            client.post(
                path,
                data={"csrf_token": token(client), "reason": REASON, "confirm": "yes"},
            ).status_code
            == 302
        )
    for role in ("gerente", "atendente"):
        sid = db.execute(
            "INSERT INTO staff_users(name,email,password_hash,role,can_settings,created_at) VALUES(?,?,?, ?,1,?) RETURNING id",
            ("Fixture " + role, role + "@example.test", "fixture", role, ps.iso(START)),
        ).fetchone()["id"]
        db.commit()
        with client.session_transaction() as s:
            s.clear()
            s.update(staff=True, staff_user_id=sid, staff_session_version=1)
        for path in routes:
            assert client.get(path).status_code == 403
    assert not db.execute("SELECT 1 FROM plan_admin_decisions").fetchone()


def test_confirmation_reason_csrf_and_operation_id_are_required(site):
    _, client, db, _ = site
    staff(client)
    path = "/painel/configuracoes/plano/clientes/1/validar"
    form = fields(client, path)
    for patch in ({"confirm": ""}, {"reason": "short"}, {"operation_id": "invalid"}):
        assert client.post(path, data={**form, **patch}).status_code == 400
    assert client.post(path, data={**form, "csrf_token": ""}).status_code == 400
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone()


def test_all_clients_search_and_manual_enrollment_without_fastflow_ready(site):
    app, client, db, fake = site
    app.config["FASTFLOW_ENABLED"] = "0"
    staff(client)
    page = client.get("/painel/configuracoes/plano?q=CLIPCA-1")
    assert page.status_code == 200 and "Validar participação" in page.text
    path = "/painel/configuracoes/plano/clientes/1/validar"
    assert client.post(path, data=fields(client, path)).status_code == 302
    assert fake.creates == 0 and fake.gets == 0
    cycle = db.execute("SELECT * FROM plan_cycles").fetchone()
    db.execute("UPDATE plan_cycles SET granted_at=NULL")
    db.execute("DELETE FROM plan_credit_ledger")
    db.commit()
    assert reconcile(db, app.config, datetime.now(TZ)) == 0
    assert db.execute("SELECT granted_at FROM plan_cycles").fetchone()["granted_at"]


def test_manual_approval_preserves_old_packages_and_consumes_only_new_receiving(site):
    _, _, db, _ = site
    old = insert_package(db, 1)
    db.commit()
    approve_direct(db)
    db.begin_write()
    new = insert_package(db, 2)
    assert ps.consume(db, 1, new, "PCTPCA-2", "Pequeno", 4.9, START)
    db.commit()
    assert (
        db.execute("SELECT base_price FROM packages WHERE id=?", (old,)).fetchone()[
            "base_price"
        ]
        == 4.9
    )
    assert (
        db.execute("SELECT base_price FROM packages WHERE id=?", (new,)).fetchone()[
            "base_price"
        ]
        == 0
    )
    assert (
        db.execute("SELECT used_credits FROM plan_cycles").fetchone()["used_credits"]
        == 1
    )


def test_client_preview_operation_concurrency_serializes_grants(site):
    app, _, db, _ = site
    key = ps.next_cycle_key(db, 1, START)
    op = str(uuid.uuid4())
    db.commit()

    def grant(_):
        conn = DatabaseConnection(app.config["DATABASE"])
        try:
            return approve_direct(conn, operation=op, expected=key)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(grant, range(3)))
    assert len(set(ids)) == 1
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1
    assert (
        db.execute("SELECT COUNT(*) n FROM plan_admin_decisions").fetchone()["n"] == 1
    )


def test_cancellation_without_confirmed_id_only_closes_definitively_failed_request(
    site,
):
    app, client, db, fake = site
    order = reserve(db)
    db.execute("UPDATE plan_orders SET state='failed' WHERE id=?", (order["id"],))
    db.commit()
    app.config["FASTFLOW_ENABLED"] = "0"
    staff(client)
    path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/cancel"
    assert client.post(path, data=fields(client, path)).status_code == 302
    assert (
        db.execute("SELECT state FROM plan_orders").fetchone()["state"]
        == "admin_cancelled"
    )
    assert fake.gets == 0 and fake.creates == 0


def test_uncertain_request_without_remote_match_is_not_closed(site):
    _, client, db, fake = site
    order = reserve(db)
    db.execute("UPDATE plan_orders SET state='uncertain' WHERE id=?", (order["id"],))
    db.commit()
    staff(client)
    path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/cancel"
    response = client.post(path, data=fields(client, path),follow_redirects=True)
    assert response.status_code == 200 and "Cancelamento aguardando verificação" in response.text
    assert (
        db.execute("SELECT state FROM plan_orders").fetchone()["state"] == "uncertain"
    )
    assert db.execute("SELECT result FROM plan_admin_decisions").fetchone()["result"]=="awaiting_verification"


def test_payment_races_remote_cancel_and_payment_is_preserved(site, monkeypatch):
    _, client, db, fake = site
    order, data = create_cycle(db, fake, state="pending")
    staff(client)
    monkeypatch.setattr(
        fake,
        "cancel",
        lambda tid: fake.records[tid].update(status="paid"),
        raising=False,
    )
    path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/cancel"
    assert client.post(path, data=fields(client, path)).status_code == 400
    assert db.execute("SELECT state FROM plan_orders").fetchone()["state"] == "paid"
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1
    assert not db.execute("SELECT 1 FROM plan_admin_decisions").fetchone()


def test_current_staff_identity_and_audit_persist_after_restart(site):
    from app import create_app

    app, client, db, _ = site
    sid = db.execute(
        "INSERT INTO staff_users(name,email,password_hash,role,can_settings,created_at) VALUES('Admin identificada','admin-fixture@example.test','fixture','administrador',1,?) RETURNING id",
        (ps.iso(START),),
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True, staff_user_id=sid, staff_session_version=1)
    path = "/painel/configuracoes/plano/clientes/1/validar"
    assert client.post(path, data=fields(client, path)).status_code == 302
    evidence = dict(db.execute("SELECT * FROM plan_admin_decisions").fetchone())
    assert (
        evidence["actor_id"] == sid and evidence["actor_name"] == "Admin identificada"
    )
    create_app(dict(app.config))
    assert dict(db.execute("SELECT * FROM plan_admin_decisions").fetchone()) == evidence
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 1


@pytest.mark.skipif(
    not __import__("os").environ.get("TEST_DATABASE_URL"),
    reason="PostgreSQL isolado não configurado",
)
def test_postgres_manual_approval_serializes_same_preview():
    import os
    from app import create_app
    from test_app import signature
    from contract import CONTRACT_VERSION
    from app import terms_text
    from werkzeug.security import generate_password_hash

    path = __import__("os").environ["TEST_DATABASE_URL"]
    app = create_app({**CONFIG, "DATABASE": path})
    db = DatabaseConnection(path)
    # Fully fictitious, isolated client, with actual fixture acceptance evidence.
    cid = db.execute(
        """INSERT INTO clients(name,cpf,phone,email,password_hash,terms_version,terms_text,signature,accepted_at,
       residential_cep,residential_street,residential_number,residential_district,residential_city,residential_state,
       confirmation_channel,confirmation_destination,confirmation_validated_at)
       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
        (
            "Admin plan fixture " + str(uuid.uuid4()),
            "93541134780",
            "16999999999",
            "manual-fixture@example.test",
            generate_password_hash("fixture-password"),
            CONTRACT_VERSION,
            terms_text(),
            signature(),
            ps.iso(START),
            "14400000",
            "Rua Fictícia",
            "10",
            "Centro",
            "Franca",
            "SP",
            "email",
            "manual-fixture@example.test",
            ps.iso(START),
        ),
    ).fetchone()["id"]
    db.commit()
    op = str(uuid.uuid4())
    key = ps.next_cycle_key(db, cid, START)
    db.commit()

    def grant(_):
        conn = DatabaseConnection(path)
        try:
            return admin_service.approve(
                conn,
                cid,
                None,
                ACTOR,
                REASON,
                op,
                START,
                Settings.from_config(CONFIG),
                expected_key=key,payment_method="Dinheiro",payment_confirmed=True,
            )
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(grant, range(3)))
    assert len(set(ids)) == 1
    assert (
        db.execute(
            "SELECT COUNT(*) n FROM plan_cycles WHERE client_id=?", (cid,)
        ).fetchone()["n"]
        == 1
    )
    assert (
        db.execute(
            "SELECT COUNT(*) n FROM plan_admin_decisions WHERE client_id=?", (cid,)
        ).fetchone()["n"]
        == 1
    )
    db.close()


def test_admin_search_filters_orders_and_unpaid_requests(site):
    _, client, db, fake = site
    order, data = create_cycle(db, fake, state="pending")
    staff(client)
    page = client.get("/painel/configuracoes/plano?q=CLIPCA-1&only_unpaid=1")
    assert (
        page.status_code == 200
        and order["id"] in page.text
        and "Cancelar solicitação" not in page.text
    )
    page = client.get("/painel/configuracoes/plano?q=cliente-inexistente&only_unpaid=1")
    assert page.status_code == 200 and order["id"] not in page.text
    data["status"]="paid";ps.apply_transaction(db,order["id"],data,START)
    page = client.get("/painel/configuracoes/plano?q=CLIPCA-1&only_unpaid=1")
    assert "Cancelar solicitação" not in page.text
    assert (
        client.get("/painel/configuracoes/plano?orders_page=invalid").status_code == 400
    )


@pytest.mark.skipif(
    not __import__("os").environ.get("TEST_DATABASE_URL"),
    reason="PostgreSQL isolado não configurado",
)
@pytest.mark.parametrize("confirmed", [True, False])
def test_postgres_cancel_saves_confirmed_or_pending_result(site, monkeypatch, confirmed):
    import os
    import plan_routes
    from app import create_app

    _, _, source, fake = site
    path = os.environ["TEST_DATABASE_URL"]
    app = create_app({**CONFIG, "DATABASE": path})
    db = DatabaseConnection(path)
    try:
        client = dict(source.execute("SELECT * FROM clients WHERE id=1").fetchone())
        client.pop("id")
        client["public_id"] = "cancel-fixture-" + str(uuid.uuid4())
        client["email"] = client["public_id"] + "@example.test"
        client["cpf"] = "11144477735"
        client["confirmation_destination"] = client["email"]
        columns = list(client)
        cid = db.execute(
            "INSERT INTO clients (" + ",".join(columns) + ") VALUES ("
            + ",".join("?" for _ in columns) + ") RETURNING id",
            tuple(client.values()),
        ).fetchone()["id"]
        db.commit()
        owner = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
        settings = Settings.from_config(CONFIG)
        order, _ = ps.reserve_order(db, owner, "pix", START, settings)
        fake.creates = uuid.uuid4().int % 1_000_000_000
        data = fake.create(order, owner)
        ps.apply_transaction(db, order["id"], data, START)
        monkeypatch.setattr(plan_routes, "gateway", lambda app: fake)

        def cancel(tid):
            if not confirmed:
                raise GatewayError(code="rejected", http_status=403)
            fake.records[tid]["status"] = "cancelled"

        monkeypatch.setattr(fake, "cancel", cancel, raising=False)
        admin_service.cancel(
            db, app, order["id"], ACTOR, REASON, str(uuid.uuid4()), START, settings
        )
        saved = db.execute(
            "SELECT * FROM plan_orders WHERE id=?", (order["id"],)
        ).fetchone()
        assert saved["cancel_state"] == ("closed" if confirmed else "external_pending")
        assert (saved["qr_text"] is None) == confirmed
        assert saved["state"] == ("admin_cancelled" if confirmed else "pending")
        next_order, _ = ps.reserve_order(db, owner, "pix", START, settings)
        assert (next_order["id"] != order["id"]) == confirmed
    finally:
        db.close()
