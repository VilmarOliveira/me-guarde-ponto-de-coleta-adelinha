"""Client and administrator local plan payments."""

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
from payment_domain import LocalSettings, PaymentError
from client_compliance import compliance
import client_actions
import plan_service as service
import plan_pricing as pricing
import payment_policies as policies
import plan_administration as administration
import uuid
from database import DatabaseIntegrityError
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
    "historical_entitlement": "Participação anterior preservada",
    "cancel_requested": "Cancelamento aguardando verificação",
    "admin_cancelled": "Solicitação cancelada pelo administrador",
}


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
        return LocalSettings.from_config(app.config)

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
                "SELECT * FROM plan_orders WHERE client_id=? AND state!='historical_entitlement' ORDER BY created_at DESC LIMIT 30",
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
            card_ready=False,
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
            return redirect(url_for("client_actions.hub"))
        if request.form.get("plan_rules") != "yes":
            flash("Confirme a leitura das regras do plano.", "error")
            return redirect(url_for("plans.client_hub"))
        _, _, history = service.cycles(db(), person["id"], now())
        scope = "renewal" if history else "subscription"
        pending = (
            db()
            .execute(
                "SELECT id FROM plan_orders WHERE client_id=? AND state IN ('creating','pending','uncertain','approved','under_review') ORDER BY created_at DESC LIMIT 1",
                (person["id"],),
            )
            .fetchone()
        )
        if pending:
            return redirect(url_for("plans.payment", oid=pending["id"]))
        choice = policies.select(
            db(), app, scope, pricing.current(db()), client_id=person["id"]
        )
        if not choice:
            flash("Nenhuma modalidade disponível. Procure o ponto.", "error")
            return redirect(url_for("plans.client_hub"))
        order, mode = service.reserve_order(
            db(),
            person,
            "administrative",
            now(),
            settings(),
            expected_amount=request.form.get("price_cents"),
        )
        if not order:
            flash(mode, "info")
            return redirect(url_for("plans.client_hub"))
        if mode == "create":
            choice = policies.select(
                db(), app, scope, order["amount_cents"], client_id=person["id"]
            )
            if not choice:
                abort(409)
            db().execute(
                "UPDATE plan_orders SET method='administrative',local_method=?,policy_snapshot=?,qr_text=?,state='pending' WHERE id=?",
                (
                    choice["method"],
                    json.dumps(choice, ensure_ascii=False),
                    policies.local_code(db(), choice["method"], order["amount_cents"]),
                    order["id"],
                ),
            )
            db().commit()
        return redirect(url_for("plans.payment", oid=order["id"]))

    @bp.get("/cliente/plano/pagamentos/<oid>")
    @client_required
    def payment(oid):
        get_order(oid, owner()["id"])
        return view(oid)

    @bp.post("/cliente/plano/pagamentos/<oid>/consultar")
    @client_required
    def payment_check(oid):
        get_order(oid, owner()["id"])
        return redirect(url_for("plans.payment", oid=oid))

    @bp.get("/cliente/plano/pagamentos/<oid>/status")
    @client_required
    def payment_status(oid):
        person = owner()
        order = get_order(oid, person["id"])
        order = get_order(oid, person["id"])
        return {
            "state": order["state"],
            "label": (
                "Verificando cobrança"
                if order["state"] in ("creating", "uncertain", "cancel_requested")
                or order["error_code"]
                else (
                    "QR Code vencido · Verificando cobrança"
                    if False
                    else LABELS.get(order["state"], "Em consulta")
                )
            ),
            "done": order["state"] not in service.OPEN_STATES,
            "pix_available": bool(service.payment_code(order, now())),
            "pix_expired": False,
        }

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
        order_filter += " AND o.state!='historical_entitlement'"
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
        events = []
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
            card_configured=False,
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
            except (ValueError, PaymentError) as failure:
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

    @bp.route("/painel/configuracoes/plano/pagamentos/<oid>", methods=["GET", "POST"])
    @admin_required
    def admin_payment(oid):
        admin()
        item = get_order(oid)
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
        )

    @bp.get("/painel/configuracoes/plano/pagamentos/<oid>/status")
    @admin_required
    def admin_payment_status(oid):
        admin()
        get_order(oid)
        item = get_order(oid)
        return {
            "state": item["state"],
            "label": LABELS.get(item["state"], "Em consulta"),
            "done": item["state"] not in service.OPEN_STATES,
            "pix_available": bool(service.payment_code(item, now())),
        }

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
        doc = (
            db()
            .execute(
                "SELECT document FROM preserved_plan_documents WHERE id=? AND client_id=?",
                (oid, owner()["id"]),
            )
            .fetchone()
        )
        if doc:
            return Response(
                bytes(doc["document"]),
                mimetype="application/pdf",
                headers={"Cache-Control": "private,no-store"},
            )
        return receipt_response(get_order(oid, owner()["id"]))

    @bp.get("/painel/configuracoes/plano/pagamentos/<oid>/comprovante.pdf")
    @admin_required
    def admin_receipt(oid):
        admin()
        doc = (
            db()
            .execute("SELECT document FROM preserved_plan_documents WHERE id=?", (oid,))
            .fetchone()
        )
        if doc:
            return Response(
                bytes(doc["document"]),
                mimetype="application/pdf",
                headers={"Cache-Control": "private,no-store"},
            )
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
            "plan_feature_visible": True,
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
            "plan_documents": (
                db()
                .execute(
                    "SELECT id,created_at FROM preserved_plan_documents WHERE client_id=? ORDER BY created_at DESC",
                    (session["client_id"],),
                )
                .fetchall()
                if session.get("client_id")
                else []
            ),
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
                    "method": pending["local_method"] or "Pagamento",
                    "title": pending["local_method"] or "Pagamento",
                    "text": "",
                }
            )
            if pending["state"] in ("creating", "uncertain", "cancel_requested"):
                choice = {
                    **choice,
                    "title": "Verificando cobrança",
                    "text": "Consulte a cobrança existente antes de trocar de modalidade.",
                }
        return {
            "customer_plan_choice": choice,
            "customer_plan_pending": dict(pending) if pending else None,
        }

    app.register_blueprint(bp)
