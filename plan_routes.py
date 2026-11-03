"""Client/admin plan screens and signed FastFlow callbacks; no PAN persistence."""

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
}


def gateway(app):
    def rate_guard(method, path):
        connection = DatabaseConnection(app.config["DATABASE"])
        try:
            service.claim_api_slot(connection, method, path, datetime.now(TZ))
        finally:
            connection.close()

    return FastFlow(Settings.from_config(app.config), rate_guard=rate_guard)


def refresh(db, app, order_id, value, *, force=False):
    row = db.execute("SELECT * FROM plan_orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        return False
    order = dict(row)
    if (
        force
        and order["checked_at"]
        and service.parse(order["checked_at"]) > value - timedelta(seconds=5)
    ):
        return False
    if (
        not force
        and order["check_after"]
        and service.parse(order["check_after"]) > value
    ):
        return False
    # Claim a short lease so repeated tabs and webhooks do not flood the API.
    claim = db.execute(
        "UPDATE plan_orders SET check_after=? WHERE id=? AND (check_after IS NULL OR check_after<=?) RETURNING id",
        (service.iso(value + timedelta(seconds=20)), order_id, service.iso(value)),
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
                "UPDATE plan_orders SET state='uncertain',checked_at=?,error_code='awaiting_correlation' WHERE id=? AND transaction_id IS NULL AND state!='paid'",
                (service.iso(value), order_id),
            )
            db.commit()
            return False
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
        code = (
            order["qr_text"]
            if order["method"] == "pix"
            and order["state"] in ("pending", "approved", "under_review")
            else None
        )
        return render_template(
            "plan_payment.html",
            order=order,
            cycle=cycle,
            labels=LABELS,
            qr=qr_data(code) if code else None,
            plan_rules=service.PLAN_RULES,
        )

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
            plan_rules=service.PLAN_RULES,
            logo=bool(app.config.get("ME_GUARDE_MAIS_LOGO_PATH")),
        )

    @bp.post("/cliente/plano/assinar")
    @client_required
    def purchase():
        person = owner()
        if session.get("staff"):
            abort(403)
        if not settings().ready:
            flash("Pagamentos do plano ainda não estão habilitados.", "error")
            return redirect(url_for("plans.client_hub"))
        if eligible(db(), person, now()):
            flash(
                "Resolva as pendências necessárias antes de contratar ou renovar o plano.",
                "error",
            )
            return redirect(url_for("client_actions.hub"))
        if request.form.get("plan_rules") != "yes":
            flash("Confirme a leitura das regras do plano.", "error")
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
        order, mode = service.reserve_order(db(), person, method, now(), settings())
        if not order:
            flash(mode, "info")
            return redirect(url_for("plans.client_hub"))
        if mode != "create":
            return redirect(url_for("plans.payment", oid=order["id"]))
        created = False
        try:
            api = gateway(app)
            data = api.create(order, person, card)
            created = True
            tid = service.attach_creation_id(db(), order["id"], data, now())
            # Card In's documented POST is a summary without the callback.
            # A fresh detail GET proves the complete binding before any grant.
            service.apply_transaction(db(), order["id"], api.get(tid), now())
        except (GatewayError, DatabaseIntegrityError) as error:
            db().rollback()
            code = getattr(error, "code", "database_conflict")
            state = (
                "uncertain"
                if getattr(error, "uncertain", False) or created
                else "declined" if code == "declined" else "failed"
            )
            db().execute(
                "UPDATE plan_orders SET state=?,error_code=? WHERE id=? AND state=?",
                (state, code, order["id"], "creating"),
            )
            db().commit()
            flash(
                (
                    "A criação precisa ser confirmada com a FastFlow. Consulte este pedido; não faremos outra cobrança automaticamente."
                    if state == "uncertain"
                    else (
                        str(error)
                        if isinstance(error, GatewayError)
                        else "Não foi possível concluir. Consulte este pedido antes de tentar outro pagamento."
                    )
                ),
                "error",
            )
        finally:
            card = None
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

    @bp.get("/cliente/plano/pagamentos/<oid>/status")
    @client_required
    def payment_status(oid):
        person = owner()
        order = get_order(oid, person["id"])
        refresh(db(), app, oid, now())
        order = get_order(oid, person["id"])
        return {
            "state": order["state"],
            "label": LABELS.get(order["state"], "Em consulta"),
            "done": order["state"] not in service.OPEN_STATES,
        }

    @bp.post("/webhooks/fastflow")
    @bp.post("/webhooks/fastflow/pedidos/<oid>")
    def webhook(oid=None):
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
        orders = (
            db()
            .execute(
                "SELECT o.*,c.name,c.public_id FROM plan_orders o JOIN clients c ON c.id=o.client_id ORDER BY o.created_at DESC LIMIT 100"
            )
            .fetchall()
        )
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
        return render_template(
            "plan_admin.html",
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

    @bp.post("/painel/configuracoes/plano/verificar-provedor")
    @admin_required
    def verify_provider():
        admin()
        if not settings().ready:
            flash(
                "Configure e habilite a integração FastFlow no ambiente do serviço.",
                "error",
            )
        else:
            try:
                gateway(app).provider()
                flash("Chave validada: provedor FastFlow.", "success")
            except GatewayError as error:
                flash(str(error), "error")
        return redirect(url_for("plans.admin_hub"))

    @bp.post("/painel/configuracoes/plano/pagamentos/<oid>/consultar")
    @admin_required
    def admin_check(oid):
        admin()
        get_order(oid)
        checked = refresh(db(), app, oid, now(), force=True)
        flash(
            (
                "Consulta concluída. A liberação depende exclusivamente da confirmação válida na API."
                if checked
                else "Aguarde a janela de consulta ou a recuperação da FastFlow; nenhum estado foi aprovado manualmente."
            ),
            "info",
        )
        return redirect(url_for("plans.admin_hub"))

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
            "plan_package_coverage": lambda pid: service.coverage(db(), pid),
        }

    app.register_blueprint(bp)
