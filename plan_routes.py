"""Client/admin plan screens and signed Fast callbacks; no PAN persistence."""

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    Response,
)
from fastflow_gateway import FastFlow, Settings, GatewayError
from client_compliance import compliance
import client_actions
import plan_service as service
import plan_pricing as pricing
import billing_archive
import payment_policies as policies
import plan_administration as administration
import uuid
from database import DatabaseIntegrityError, DatabaseConnection
from core import TZ

LABELS = {
    "creating": "Preparando cobrança",
    "uncertain": "Confirmação em consulta",
    "pending": "Aguardando pagamento",
    "approved": "Em análise de confirmação",
    "under_review": "Em análise",
    "paid": "Pagamento confirmado",
    "expired": "Cobrança expirada",
    "cancelled": "Cobrança cancelada",
    "declined": "Cartão recusado",
    "failed": "Não foi possível criar a cobrança",
    "refunded": "Estornado / em tratamento",
    "manual_approved": "Participação validada manualmente",
    "cancel_requested": "Cancelamento aguardando verificação",
    "admin_cancelled": "Solicitação cancelada pelo administrador",
}


def gateway(app):
    def rate_guard(method, path):
        connection = DatabaseConnection(app.config["DATABASE"])
        try:
            service.claim_api_slot(connection, method, path, datetime.now(TZ))
        finally:
            connection.close()

    return FastFlow(Settings.from_config(app.config), rate_guard=rate_guard)


def replaceable_pix(order):
    return (
        order["method"] == "pix"
        and order["local_method"] == "Pix Fast"
        and order["state"] == "pending"
        and bool(order["transaction_id"])
        and not any(
            order[key]
            for key in (
                "paid_at", "manual_paid_at", "refunded_at", "archived_at", "cancel_state"
            )
        )
    )


def refresh(db, app, order_id, value, *, force=False, replace_pix=False):
    row = db.execute("SELECT * FROM plan_orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        return False
    order = dict(row)
    if order["method"] == "administrative":
        return False
    if replace_pix and not replaceable_pix(order):
        return False
    if (
        force
        and not replace_pix
        and order["checked_at"]
        and service.parse(order["checked_at"]) > value - timedelta(seconds=5)
    ):
        return False
    if (
        not force
        and not replace_pix
        and order["check_after"]
        and service.parse(order["check_after"]) > value
    ):
        return False
    # An explicit replacement can skip a completed normal consultation's
    # cooldown, but not a request in progress or the backoff after an error.
    if (
        replace_pix
        and order["checked_at"]
        and order["check_after"] == service.iso(
            service.parse(order["checked_at"]) + timedelta(seconds=20)
        )
        and not order["error_code"]
    ):
        claim = db.execute(
            "UPDATE plan_orders SET check_after=? WHERE id=? AND check_after=? RETURNING id",
            (service.iso(value + timedelta(seconds=90)), order_id, order["check_after"]),
        ).fetchone()
    else:
        claim = db.execute(
            "UPDATE plan_orders SET check_after=? WHERE id=? AND (check_after IS NULL OR check_after<=?) RETURNING id",
            (
                service.iso(value + timedelta(seconds=90 if replace_pix else 20)),
                order_id,
                service.iso(value),
            ),
        ).fetchone()
    db.commit()
    if not claim:
        return False
    try:
        api = gateway(app)
        data = (
            api.get(order["transaction_id"])
            if order["transaction_id"]
            else api.find(order)
        )
        if data is None:
            db.execute(
                "UPDATE plan_orders SET state='uncertain',checked_at=?,error_code='awaiting_correlation' WHERE id=? AND transaction_id IS NULL AND state IN ('creating','uncertain','pending','approved','under_review')",
                (service.iso(value), order_id),
            )
            db.commit()
            return False
        if replace_pix and data.get("status") == "pending":
            current = db.execute(
                "SELECT * FROM plan_orders WHERE id=?", (order_id,)
            ).fetchone()
            if not current or not replaceable_pix(current):
                return False
            tid = service.validate_transaction(dict(current), data)
            db.commit()
            api.cancel(tid)
            data = api.get(tid)
        service.apply_transaction(db, order_id, data, value)
        return True
    except (GatewayError, DatabaseIntegrityError) as error:
        code = getattr(error, "code", "database_conflict")
        db.rollback()
        db.execute(
            "UPDATE plan_orders SET checked_at=?,error_code=?,check_after=? WHERE id=?",
            (
                service.iso(value),
                code,
                service.iso(value + timedelta(minutes=2)),
                order_id,
            ),
        )
        db.commit()
        if code in (
            "identity_mismatch",
            "amount_provider_mismatch",
            "duplicate_remote",
            "unknown_status",
        ):
            db.execute(
                "UPDATE plan_cycles SET suspended=1,review_required=1 WHERE order_id=?",
                (order_id,),
            )
            db.commit()
        return False


def eligible(db, owner, value):
    missing = [row["label"] for row in compliance(db, owner)[1]]
    blocked = client_actions.blockers(db, owner, service.iso(value)).get("plans", [])
    return missing + blocked


def install(
    app,
    db,
    client_required,
    admin_required,
    current_staff,
    now,
    qr_data,
    validate_image,
):
    bp = Blueprint("plans", __name__)

    def owner():
        return (
            db()
            .execute("SELECT * FROM clients WHERE id=?", (session["client_id"],))
            .fetchone()
        )

    def admin():
        staff = current_staff()
        if staff and staff["role"] != "administrador":
            abort(403)

    def settings():
        return Settings.from_config(app.config)

    def get_order(oid, client_id=None):
        row = (
            db()
            .execute(
                "SELECT * FROM plan_orders WHERE id=?"
                + (" AND client_id=?" if client_id is not None else ""),
                (oid, client_id) if client_id is not None else (oid,),
            )
            .fetchone()
        )
        if not row:
            abort(404)
        return dict(row)

    def activate(client_id):
        db().commit()
        db().begin_write()
        service.lock_account(db(), client_id, now())
        service.grant_due(db(), client_id, now())
        db().commit()

    def card_ready():
        cfg = settings()
        if not (cfg.ready and cfg.card_enabled and cfg.card_pci_ready):
            return False
        try:
            return gateway(app).card_available()
        except GatewayError:
            return False

    def view(oid):
        person = owner()
        order = get_order(oid, person["id"])
        cycle = (
            db()
            .execute("SELECT * FROM plan_cycles WHERE order_id=?", (oid,))
            .fetchone()
        )
        value = now()
        code = service.payment_code(order, value)
        return render_template(
            "plan_payment.html",
            order=order,
            cycle=cycle,
            labels=LABELS,
            qr=qr_data(code) if code else None,
            pix_window_ended=service.fast_pix_window_ended(order, value),
            pix_expires_at=service.safe_time(order["expires_at"]),
            can_retry=order["local_method"] == "Pix Fast"
            and order["state"] in ("expired", "cancelled")
            and not order["paid_at"]
            and not order["archived_at"]
            and not order["error_code"]
            and not order["cancel_state"],
            can_replace_pix=replaceable_pix(order),
            plan_rules=order["rules_text"],
            payment_choice=(
                json.loads(order["policy_snapshot"])
                if order["policy_snapshot"]
                else None
            ),
        )

    @bp.get("/plano/assinar")
    def subscribe_entry():
        if session.get("staff"):
            flash("A contratação do plano é feita pela conta do cliente.", "info")
            return redirect(url_for("dashboard"))
        if session.get("client_id"):
            person = owner()
            if (
                person
                and person["active"]
                and person["session_version"] == session.get("client_session_version")
            ):
                return redirect(url_for("plans.client_hub"))
            session.pop("client_id", None)
            session.pop("client_session_version", None)
        session["client_login_destination"] = "plan"
        return redirect(url_for("client_login"))

    @bp.get("/cliente/plano")
    @client_required
    def client_hub():
        person = owner()
        activate(person["id"])
        current, future, history = service.cycles(db(), person["id"], now())
        orders = (
            db()
            .execute(
                "SELECT * FROM plan_orders WHERE client_id=? ORDER BY created_at DESC LIMIT 30",
                (person["id"],),
            )
            .fetchall()
        )
        return render_template(
            "plan_client.html",
            current=current,
            future=future,
            cycles=history,
            orders=orders,
            labels=LABELS,
            ready=settings().ready,
            card_ready=card_ready(),
            missing=eligible(db(), person, now()),
            ledger=db()
            .execute(
                "SELECT l.*,p.public_id FROM plan_credit_ledger l JOIN plan_cycles c ON c.id=l.cycle_id LEFT JOIN packages p ON p.id=l.package_id WHERE c.client_id=? ORDER BY l.id DESC LIMIT 100",
                (person["id"],),
            )
            .fetchall(),
            plan_rules=service.offered_rules(db()),
            logo=bool(app.config.get("ME_GUARDE_MAIS_LOGO_PATH")),
        )

    @bp.post("/painel/configuracoes/plano/mensalidade")
    @admin_required
    def update_monthly_price():
        admin()
        try:
            cents = pricing.parse(request.form.get("monthly_price", ""))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect(url_for("plans.admin_hub"))
        previous = pricing.current(db())
        db().execute("UPDATE settings SET monthly_price_cents=? WHERE id=1", (cents,))
        db().execute(
            "INSERT INTO audit_logs(actor,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
            (
                actor()["key"],
                "alterar_mensalidade",
                "plano",
                1,
                json.dumps({"previous_cents": previous, "new_cents": cents}),
                service.iso(now()),
            ),
        )
        db().commit()
        flash("Mensalidade atualizada com sucesso.", "success")
        return redirect(url_for("plans.admin_hub"))

    @bp.post("/cliente/plano/assinar")
    @client_required
    def purchase():
        person = owner()
        if session.get("staff"):
            abort(403)
        if eligible(db(), person, now()):
            flash(
                "Resolva as pendências necessárias antes de contratar ou renovar o plano.",
                "error",
            )
            return redirect(url_for("client_actions.hub"))
        if request.form.get("plan_rules") != "yes":
            flash("Confirme a leitura das regras do plano.", "error")
            return redirect(url_for("plans.client_hub"))
        current, upcoming, history = service.cycles(db(), person["id"], now())
        scope = "renewal" if history else "subscription"
        cycle_key = service.next_cycle_key(db(), person["id"], now())
        pending = (
            db()
            .execute(
                "SELECT * FROM plan_orders WHERE client_id=? AND (state IN ('creating','uncertain','pending','approved','under_review','cancel_requested') OR error_code IN ('archived_payment_review','payment_after_cancellation')) ORDER BY created_at DESC LIMIT 1",
                (person["id"],),
            )
            .fetchone()
        )
        if pending:
            if service.fast_pix_window_ended(pending, now()):
                refresh(db(), app, pending["id"], now())
                pending = get_order(pending["id"], person["id"])
            if pending["state"] not in ("expired", "cancelled") or pending[
                "error_code"
            ] in ("archived_payment_review", "payment_after_cancellation"):
                return redirect(url_for("plans.payment", oid=pending["id"]))
        choice = policies.select(
            db(), app, scope, pricing.current(db()), client_id=person["id"]
        )
        if not choice:
            flash("Nenhuma modalidade disponível. Procure o ponto.", "error")
            return redirect(url_for("plans.client_hub"))
        method = request.form.get("method", "pix")
        if method not in ("pix", "card"):
            abort(400)
        card = None
        if method == "card":
            if not card_ready():
                flash("Cartão não habilitado. Escolha Pix.", "error")
                return redirect(url_for("plans.client_hub"))
            # Values exist only in request memory and the outgoing TLS call.
            number = re.sub(r"\D", "", request.form.get("card_number", ""))
            cvv = request.form.get("card_cvv", "")
            expiration = request.form.get("card_expiration", "")
            holder = request.form.get("card_holder", "").strip()
            if (
                not re.fullmatch("[0-9]{13,19}", number)
                or not re.fullmatch("[0-9]{3,4}", cvv)
                or not re.fullmatch("(0[1-9]|1[0-2])/[0-9]{4}", expiration)
                or not 3 <= len(holder) <= 100
            ):
                flash("Confira os dados do cartão ou escolha Pix.", "error")
                return redirect(url_for("plans.client_hub"))
            card = {
                "number": number,
                "holder": holder,
                "expiration": expiration,
                "cvv": cvv,
            }
        manual = choice["method"] != "Pix Fast"
        order, mode = service.reserve_order(
            db(),
            person,
            "administrative" if manual else method,
            now(),
            settings(),
            expected_amount=request.form.get("price_cents"),
        )
        if not order:
            flash(mode, "info")
            return redirect(url_for("plans.client_hub"))
        if mode != "create":
            return redirect(url_for("plans.payment", oid=order["id"]))
        choice = policies.select(
            db(), app, scope, order["amount_cents"], client_id=person["id"]
        )
        if not choice:
            db().execute(
                "UPDATE plan_orders SET state='failed' WHERE id=?", (order["id"],)
            )
            db().commit()
            flash(
                "Nenhuma modalidade disponível para este valor. Procure o ponto.",
                "error",
            )
            return redirect(url_for("plans.client_hub"))
        manual = choice["method"] != "Pix Fast"
        db().execute(
            "UPDATE plan_orders SET method=? WHERE id=?",
            ("administrative" if manual else method, order["id"]),
        )
        order = {**order, "method": "administrative" if manual else method}
        code = policies.local_code(db(), choice["method"], order["amount_cents"])
        db().execute(
            "UPDATE plan_orders SET local_method=?,policy_snapshot=?,qr_text=?,state=? WHERE id=?",
            (
                choice["method"],
                json.dumps(choice, ensure_ascii=False),
                code,
                "pending" if manual else "creating",
                order["id"],
            ),
        )
        db().commit()
        if manual:
            return redirect(url_for("plans.payment", oid=order["id"]))
        import plan_charge

        problem = plan_charge.start(db(), app, order, person, now(), card)
        if problem:
            flash(problem, "error")
        return redirect(url_for("plans.payment", oid=order["id"]))

    @bp.get("/cliente/plano/pagamentos/<oid>")
    @client_required
    def payment(oid):
        get_order(oid, owner()["id"])
        refresh(db(), app, oid, now())
        return view(oid)

    @bp.post("/cliente/plano/pagamentos/<oid>/consultar")
    @client_required
    def payment_check(oid):
        get_order(oid, owner()["id"])
        refresh(db(), app, oid, now())
        return redirect(url_for("plans.payment", oid=oid))

    @bp.post("/cliente/plano/pagamentos/<oid>/novo-pix")
    @client_required
    def retry_payment(oid):
        person = owner()
        original = get_order(oid, person["id"])
        if session.get("staff"):
            abort(403)
        if eligible(db(), person, now()):
            return redirect(url_for("client_actions.hub"))
        child = (
            db()
            .execute(
                "SELECT id FROM plan_orders WHERE retry_of=? ORDER BY created_at DESC LIMIT 1",
                (oid,),
            )
            .fetchone()
        )
        if child:
            return redirect(url_for("plans.payment", oid=child["id"]))
        # Only an explicit client request may close an unpaid pending charge.
        replacing = request.form.get("replace_pending") == "yes"
        if replacing and replaceable_pix(original):
            verified = refresh(db(), app, oid, now(), replace_pix=True)
        else:
            verified = refresh(db(), app, oid, now(), force=True)
        original = get_order(oid, person["id"])
        recent = original["checked_at"] and service.parse(
            original["checked_at"]
        ) >= now() - timedelta(seconds=20)
        if (
            (not verified and not recent)
            or original["state"] not in ("expired", "cancelled")
            or original["error_code"]
        ):
            if original["state"] != "paid":
                flash(
                    "A Fast não confirmou o encerramento sem pagamento. Nenhum Pix novo foi gerado. Consulte a cobrança ou fale com o ponto se o problema continuar."
                    if replacing
                    else "Consulte novamente. Só é possível gerar outro Pix após a Fast confirmar o encerramento sem pagamento.",
                    "info",
                )
            return redirect(url_for("plans.payment", oid=oid))
        order, mode = service.reserve_order(
            db(), person, "pix", now(), settings(), retry_from=oid
        )
        if not order:
            flash(mode, "error")
            return redirect(url_for("plans.payment", oid=oid))
        if mode == "create":
            import plan_charge

            problem = plan_charge.start(db(), app, order, person, now())
            if problem:
                flash(problem, "error")
        return redirect(url_for("plans.payment", oid=order["id"]))

    @bp.get("/cliente/plano/pagamentos/<oid>/status")
    @client_required
    def payment_status(oid):
        person = owner()
        order = get_order(oid, person["id"])
        refresh(db(), app, oid, now())
        order = get_order(oid, person["id"])
        return {
            "state": order["state"],
            "label": (
                "Verificando cobrança"
                if order["state"] in ("creating", "uncertain", "cancel_requested")
                or order["error_code"]
                else (
                    "QR Code vencido · Verificando cobrança"
                    if service.fast_pix_window_ended(order, now())
                    else LABELS.get(order["state"], "Em consulta")
                )
            ),
            "done": order["state"] not in service.OPEN_STATES,
            "pix_available": bool(service.payment_code(order, now())),
            "pix_expired": service.fast_pix_window_ended(order, now()),
        }

    @bp.post("/webhooks/fastflow/retiradas/<rid>")
    @bp.post("/webhooks/fastflow")
    @bp.post("/webhooks/fastflow/pedidos/<oid>")
    def webhook(oid=None, rid=None):
        cfg = settings()
        if not cfg.ready:
            return "", 503
        if request.content_length and request.content_length > 65536:
            abort(413)
        raw = request.stream.read(65537)
        if len(raw) > 65536:
            abort(413)
        supplied = request.headers.get("X-Webhook-Signature", "")
        expected = (
            "sha256="
            + hmac.new(cfg.webhook_secret.encode(), raw, hashlib.sha256).hexdigest()
        )
        if not re.fullmatch(
            r"sha256=[a-f0-9]{64}", supplied
        ) or not hmac.compare_digest(supplied, expected):
            return "", 401
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                return "", 400
            # The provider panel sends a signed connectivity check, not a TX.
            # Do not access the database or API for this exact boolean flag.
            if payload.get("test") is True:
                return "", 200
            data = payload.get("data", payload)
            tid = str(data.get("transaction_id", data.get("id", "")))
            if not re.fullmatch("[1-9][0-9]{0,29}", tid):
                return "", 400
            status = data.get("status")
            event = (
                "transaction." + status
                if status
                in (
                    "pending",
                    "approved",
                    "paid",
                    "expired",
                    "refunded",
                    "cancelled",
                    "under_review",
                )
                else "transaction.unknown"
            )
        except (ValueError, TypeError, AttributeError):
            return "", 400
        import pickup_payments

        if rid or (
            not oid
            and db()
            .execute(
                "SELECT 1 FROM pickup_payment_orders WHERE transaction_id=?", (tid,)
            )
            .fetchone()
        ):
            return pickup_payments.webhook(db(), app, rid, tid, event, raw, now())
        order = (
            db()
            .execute(
                "SELECT * FROM plan_orders WHERE "
                + ("id=?" if oid else "transaction_id=?"),
                (oid or tid,),
            )
            .fetchone()
        )
        if not order:
            return "", 202
        if order["transaction_id"] and order["transaction_id"] != tid:
            return "", 409
        key = hashlib.sha256((order["id"] + ":" + tid + ":").encode() + raw).hexdigest()
        previous = (
            db()
            .execute(
                "SELECT outcome FROM plan_payment_events WHERE event_key=?", (key,)
            )
            .fetchone()
        )
        if previous and previous["outcome"] == "processed":
            return "", 200
        db().execute(
            """INSERT INTO plan_payment_events(order_id,transaction_id,event_key,event_type,received_at,outcome)
            VALUES(?,?,?,?,?,'pending') ON CONFLICT DO NOTHING""",
            (order["id"], tid, key, event, service.iso(now())),
        )
        db().commit()
        # A webhook may arrive before the POST response. Only attach its ID after
        # a fresh server API GET proves callback, amount, method and customer.
        try:
            fresh = gateway(app).get(tid)
            service.apply_transaction(db(), order["id"], fresh, now())
            if event == "transaction.paid" and fresh["status"] != "paid":
                return "", 202
            db().execute(
                "UPDATE plan_payment_events SET outcome='processed',processed_at=?,reason=NULL WHERE event_key=?",
                (service.iso(now()), key),
            )
            db().commit()
            return "", 200
        except GatewayError as error:
            db().rollback()
            db().execute(
                "UPDATE plan_payment_events SET outcome=?,reason=? WHERE event_key=?",
                ("pending", error.code, key),
            )
            if error.code in (
                "identity_mismatch",
                "amount_provider_mismatch",
                "duplicate_remote",
                "unknown_status",
            ):
                db().execute(
                    "UPDATE plan_cycles SET suspended=1,review_required=1 WHERE order_id=?",
                    (order["id"],),
                )
            db().commit()
            return "", 202
        except DatabaseIntegrityError:
            db().rollback()
            return "", 202

    @bp.get("/painel/configuracoes/plano")
    @admin_required
    def admin_hub():
        admin()
        try:
            orders_page = max(1, int(request.args.get("orders_page", "1")))
        except ValueError:
            abort(400)
        search = request.args.get("q", "").strip()[:100]
        matching_orders = "%" + search.lower() + "%"
        only_unpaid = request.args.get("only_unpaid") == "1"
        order_filter = (
            " AND o.state IN ('creating','uncertain','pending','approved','under_review','expired','cancelled','failed','declined','cancel_requested') AND o.paid_at IS NULL AND NOT EXISTS(SELECT 1 FROM plan_cycles pc WHERE pc.order_id=o.id)"
            if only_unpaid
            else ""
        )
        show_archived = request.args.get("arquivadas") == "1"
        order_filter += " AND o.archived_at IS " + (
            "NOT NULL" if show_archived else "NULL"
        )
        order_rows = (
            db()
            .execute(
                "SELECT o.*,c.name,c.public_id FROM plan_orders o JOIN clients c ON c.id=o.client_id WHERE (LOWER(c.name) LIKE ? OR LOWER(COALESCE(c.public_id,'')) LIKE ? OR LOWER(COALESCE(c.email,'')) LIKE ? OR LOWER(o.id) LIKE ? OR COALESCE(o.transaction_id,'') LIKE ?)"
                + order_filter
                + " ORDER BY o.created_at DESC LIMIT 101 OFFSET ?",
                (
                    matching_orders,
                    matching_orders,
                    matching_orders,
                    matching_orders,
                    matching_orders,
                    (orders_page - 1) * 100,
                ),
            )
            .fetchall()
        )
        orders = order_rows[:100]
        cycles = (
            db()
            .execute(
                "SELECT p.*,c.public_id FROM plan_cycles p JOIN clients c ON c.id=p.client_id ORDER BY starts_at DESC LIMIT 100"
            )
            .fetchall()
        )
        events = (
            db()
            .execute("SELECT * FROM plan_payment_events ORDER BY id DESC LIMIT 100")
            .fetchall()
        )
        reminders = (
            db()
            .execute("SELECT * FROM plan_reminders ORDER BY claimed_at DESC LIMIT 50")
            .fetchall()
        )
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            abort(400)
        query = request.args.get("q", "").strip()[:100]
        matching = "%" + query.lower() + "%"
        clients = (
            db()
            .execute(
                "SELECT id,name,public_id,email,active FROM clients WHERE LOWER(name) LIKE ? OR LOWER(COALESCE(public_id,'')) LIKE ? OR LOWER(COALESCE(email,'')) LIKE ? ORDER BY id DESC LIMIT 51 OFFSET ?",
                (matching, matching, matching, (page - 1) * 50),
            )
            .fetchall()
        )
        decisions = (
            db()
            .execute(
                "SELECT d.*,c.public_id FROM plan_admin_decisions d JOIN clients c ON c.id=d.client_id ORDER BY d.created_at DESC LIMIT 100"
            )
            .fetchall()
        )
        return render_template(
            "plan_admin.html",
            show_archived=show_archived,
            orders_page=orders_page,
            orders_next=len(order_rows) > 100,
            only_unpaid=only_unpaid,
            clients=clients[:50],
            page=page,
            has_next=len(clients) > 50,
            query=query,
            decisions=decisions,
            orders=orders,
            cycles=cycles,
            events=events,
            reminders=reminders,
            ledger=db()
            .execute(
                "SELECT l.*,c.client_id FROM plan_credit_ledger l JOIN plan_cycles c ON c.id=l.cycle_id ORDER BY l.id DESC LIMIT 100"
            )
            .fetchall(),
            labels=LABELS,
            ready=settings().ready,
            card_configured=settings().card_enabled and settings().card_pci_ready,
            logo=bool(app.config.get("ME_GUARDE_MAIS_LOGO_PATH")),
        )

    def actor():
        staff = current_staff()
        return {
            "id": staff["id"] if staff else None,
            "key": "staff:" + str(staff["id"]) if staff else "legacy",
            "name": (
                staff["name"]
                if staff
                else app.config.get("STAFF_NAME", "Administrador")
            ),
        }

    def decision_screen(person, order, action):
        current, future, _ = service.cycles(db(), person["id"], now())
        missing = eligible(db(), person, now()) if action == "approve" else []
        operation_id = (
            request.form.get("operation_id", "")
            if request.method == "POST"
            else str(uuid.uuid4())
        )
        error = None
        if request.method == "POST":
            try:
                if request.form.get("confirm") != "yes":
                    raise ValueError("Confirme a ação antes de continuar.")
                if (
                    not order
                    and request.form.get("price_cents") is not None
                    and request.form["price_cents"] != str(pricing.current(db()))
                ):
                    raise ValueError(
                        "A mensalidade mudou. Confira o novo valor e abra a prévia novamente."
                    )
                if action == "approve":
                    if not person["active"]:
                        raise ValueError(
                            "Cliente inativo. Regularize a conta antes de validar a participação."
                        )
                    if missing:
                        raise ValueError("Regularize antes: " + "; ".join(missing))
                    if request.form.get("payment_method") == "Pix Fast":
                        oid = administration.create_fast(
                            db(),
                            app,
                            person,
                            order,
                            actor(),
                            request.form.get("reason", ""),
                            operation_id,
                            now(),
                            settings(),
                            request.form.get("cycle_key"),
                        )
                        return redirect(url_for("plans.admin_payment", oid=oid))
                    administration.approve(
                        db(),
                        person["id"],
                        order["id"] if order else None,
                        actor(),
                        request.form.get("reason", ""),
                        operation_id,
                        now(),
                        settings(),
                        expected_key=request.form.get("cycle_key"),
                        payment_method=request.form.get("payment_method"),
                        payment_confirmed=request.form.get("payment_confirmed")
                        == "yes",
                    )
                    flash(
                        (
                            "Renovação confirmada com sucesso"
                            if current
                            else "Plano ativado com sucesso"
                        ),
                        "success",
                    )
                else:
                    administration.cancel(
                        db(),
                        app,
                        order["id"],
                        actor(),
                        request.form.get("reason", ""),
                        operation_id,
                        now(),
                        settings(),
                    )
                    closed = (
                        db()
                        .execute(
                            "SELECT state FROM plan_orders WHERE id=?", (order["id"],)
                        )
                        .fetchone()["state"]
                        == "admin_cancelled"
                    )
                    flash(
                        (
                            "Solicitação não paga cancelada. O histórico foi preservado."
                            if closed
                            else "Cancelamento aguardando verificação. A cobrança externa continua em acompanhamento."
                        ),
                        "success" if closed else "info",
                    )
                return redirect(url_for("plans.admin_hub"))
            except (ValueError, GatewayError) as failure:
                db().rollback()
                error = str(failure)
            except DatabaseIntegrityError:
                db().rollback()
                error = "O pedido mudou durante a operação. Atualize a página para conferir o estado atual."
        setting = (
            db()
            .execute(
                "SELECT pix_key,pix_key_type,pix_receiver_name FROM settings WHERE id=1"
            )
            .fetchone()
        )
        ai_code = None
        if setting["pix_key_type"] == "CNPJ" and setting["pix_receiver_name"]:
            from core import pix_payload

            ai_code = pix_payload(
                setting["pix_key"],
                setting["pix_receiver_name"],
                (
                    order["amount_cents"] / 100
                    if order
                    else service.public_offer(db())["monthly_price"]
                ),
            )
        return render_template(
            "plan_admin_decision.html",
            preview_start=service.iso(
                service.parse(current["ends_at"]) if current else now()
            ),
            preview_end=service.iso(
                service.month_after(
                    service.parse(current["ends_at"]) if current else now(),
                    current["anchor_day"] if current else None,
                )
            ),
            ai_code=ai_code,
            ai_qr=qr_data(ai_code) if ai_code else None,
            person=person,
            order=order,
            action=action,
            current=current,
            future=future,
            missing=missing,
            error=error,
            operation_id=operation_id,
            cycle_key=service.next_cycle_key(db(), person["id"], now()),
            reason=request.form.get("reason", ""),
            labels=LABELS,
        ), (400 if error else 200)

    @bp.route(
        "/painel/configuracoes/plano/clientes/<int:client_id>/validar",
        methods=["GET", "POST"],
    )
    @admin_required
    def admin_client_approve(client_id):
        admin()
        person = (
            db().execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
        )
        if not person:
            abort(404)
        return decision_screen(person, None, "approve")

    @bp.route(
        "/painel/configuracoes/plano/pedidos/<oid>/<action>", methods=["GET", "POST"]
    )
    @admin_required
    def admin_decide(oid, action):
        admin()
        if action not in ("approve", "cancel"):
            abort(404)
        order = get_order(oid)
        person = (
            db()
            .execute("SELECT * FROM clients WHERE id=?", (order["client_id"],))
            .fetchone()
        )
        return decision_screen(person, order, action)

    @bp.post("/painel/configuracoes/plano/pagamentos/<oid>/arquivar")
    @admin_required
    def archive_payment(oid):
        admin()
        try:
            billing_archive.archive(
                db(), "plan_orders", oid, actor()["key"], service.iso(now())
            )
            flash(
                "Cobrança arquivada. O acompanhamento do pagamento continua.", "success"
            )
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("plans.admin_hub"))

    @bp.route("/painel/configuracoes/plano/pagamentos/<oid>", methods=["GET", "POST"])
    @admin_required
    def admin_payment(oid):
        admin()
        item = get_order(oid)
        refresh(db(), app, oid, now())
        item = get_order(oid)
        person = (
            db()
            .execute(
                "SELECT name,public_id FROM clients WHERE id=?", (item["client_id"],)
            )
            .fetchone()
        )
        cycle = (
            db()
            .execute("SELECT * FROM plan_cycles WHERE order_id=?", (oid,))
            .fetchone()
        )
        return render_template(
            "plan_admin_payment.html",
            order=item,
            person=person,
            cycle=cycle,
            labels=LABELS,
            qr=qr_data(code) if (code := service.payment_code(item, now())) else None,
            pix_window_ended=service.fast_pix_window_ended(item, now()),
        )

    @bp.get("/painel/configuracoes/plano/pagamentos/<oid>/status")
    @admin_required
    def admin_payment_status(oid):
        admin()
        get_order(oid)
        refresh(db(), app, oid, now())
        item = get_order(oid)
        return {
            "state": item["state"],
            "label": LABELS.get(item["state"], "Em consulta"),
            "done": item["state"] not in service.OPEN_STATES,
            "pix_available": bool(service.payment_code(item, now())),
        }

    @bp.post("/painel/configuracoes/plano/verificar-provedor")
    @admin_required
    def verify_provider():
        admin()
        if not settings().ready:
            flash(
                "Configure e habilite a integração Fast no ambiente do serviço.",
                "error",
            )
        else:
            try:
                gateway(app).provider()
                flash("Chave validada: provedor Fast.", "success")
            except GatewayError as error:
                flash(str(error), "error")
        return redirect(url_for("plans.admin_hub"))

    @bp.post("/painel/configuracoes/plano/pagamentos/<oid>/consultar")
    @admin_required
    def admin_check(oid):
        admin()
        order = get_order(oid)
        if order["method"] == "administrative":
            flash(
                "Esta participação foi validada manualmente e não possui cobrança Fast para consulta.",
                "info",
            )
            return redirect(url_for("plans.admin_hub"))
        checked = refresh(db(), app, oid, now(), force=True)
        flash(
            (
                "Consulta concluída. A liberação depende exclusivamente da confirmação válida na API."
                if checked
                else "Aguarde a janela de consulta ou a recuperação da Fast; nenhum estado foi aprovado manualmente."
            ),
            "info",
        )
        return redirect(url_for("plans.admin_hub"))

    def receipt_response(item):
        from flask import send_file
        from plan_receipt import build

        person = (
            db()
            .execute(
                "SELECT name,public_id FROM clients WHERE id=?", (item["client_id"],)
            )
            .fetchone()
        )
        cycle = (
            db()
            .execute("SELECT * FROM plan_cycles WHERE order_id=?", (item["id"],))
            .fetchone()
        )
        method = item.get("local_method") or (
            "Pix" if item["method"] == "pix" else "Validação administrativa"
        )
        decision = (
            db()
            .execute(
                "SELECT actor_name FROM plan_admin_decisions WHERE order_id=? ORDER BY created_at DESC LIMIT 1",
                (item["id"],),
            )
            .fetchone()
        )
        return send_file(
            build(
                item,
                person,
                cycle,
                method,
                decision["actor_name"] if decision else None,
            ),
            mimetype="application/pdf",
            as_attachment=True,
            download_name="comprovante-plano-" + item["id"] + ".pdf",
        )

    @bp.get("/cliente/plano/pagamentos/<oid>/comprovante.pdf")
    @client_required
    def receipt(oid):
        return receipt_response(get_order(oid, owner()["id"]))

    @bp.get("/painel/configuracoes/plano/pagamentos/<oid>/comprovante.pdf")
    @admin_required
    def admin_receipt(oid):
        admin()
        return receipt_response(get_order(oid))

    @bp.get("/plano/me-guarde-mais/logo")
    def logo():
        path = app.config.get("ME_GUARDE_MAIS_LOGO_PATH", "")
        try:
            data = (
                Path(path).read_bytes()
                if path
                and Path(path).is_file()
                and Path(path).stat().st_size <= 900 * 1024
                else None
            )
        except OSError:
            data = None
        mime = validate_image(data, 900 * 1024)
        if not mime:
            abort(404)
        return Response(
            data,
            mimetype=mime,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "public, max-age=86400",
            },
        )

    @app.context_processor
    def plan_context():
        has_history = (
            session.get("client_id")
            and db()
            .execute(
                "SELECT 1 FROM plan_orders WHERE client_id=? LIMIT 1",
                (session["client_id"],),
            )
            .fetchone()
        )
        return {
            "plan_feature_visible": settings().active or bool(has_history),
            "plan_labels": LABELS,
            "plan_payment_method": lambda order: dict(order).get("local_method")
            or (
                "Pix"
                if order["method"] == "pix"
                else (
                    "Validação administrativa"
                    if order["method"] == "administrative"
                    else "Cartão"
                )
            ),
            "public_plan_offer": service.public_offer(db()),
            "public_plan_logo": bool(
                app.config.get("ME_GUARDE_MAIS_LOGO_PATH")
                and Path(app.config["ME_GUARDE_MAIS_LOGO_PATH"]).is_file()
            ),
            "plan_package_coverage": lambda pid: service.coverage(db(), pid),
            "package_coverage_description": lambda package: service.coverage_description(
                db(), package
            ),
        }

    @app.context_processor
    def payment_choice_context():
        if not session.get("client_id"):
            return {}
        person = owner()
        if not person:
            return {}
        _, _, history = service.cycles(db(), person["id"], now())
        choice = policies.select(
            db(),
            app,
            "renewal" if history else "subscription",
            pricing.current(db()),
            client_id=person["id"],
        )
        pending = (
            db()
            .execute(
                "SELECT * FROM plan_orders WHERE client_id=? AND state IN ('creating','uncertain','pending','approved','under_review','cancel_requested') ORDER BY created_at DESC LIMIT 1",
                (person["id"],),
            )
            .fetchone()
        )
        if pending:
            choice = (
                json.loads(pending["policy_snapshot"])
                if pending["policy_snapshot"]
                else {
                    "method": pending["local_method"] or "Pix Fast",
                    "title": pending["local_method"] or "Pix Fast",
                    "text": "",
                }
            )
            if pending["state"] in ("creating", "uncertain", "cancel_requested"):
                choice = {
                    **choice,
                    "title": "Verificando cobrança",
                    "text": "Consulte a cobrança existente antes de trocar de modalidade.",
                }
        return {"customer_plan_choice": choice}

    app.register_blueprint(bp)
