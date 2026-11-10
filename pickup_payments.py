"""Local package prepayments; payment does not confirm physical delivery."""

import hashlib, json, uuid, re
from decimal import Decimal, InvalidOperation
from datetime import timedelta
from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from core import calculate_discount, validate_surcharge
from payment_domain import LocalSettings, PaymentError
from database import DatabaseIntegrityError
import plan_service as ps
import payment_policies as policies

OPEN = (
    "creating",
    "uncertain",
    "pending",
    "approved",
    "under_review",
    "paid",
    "cancel_requested",
)
LABELS = {
    "creating": "Preparando cobrança",
    "uncertain": "Confirmação em consulta",
    "pending": "Aguardando pagamento",
    "approved": "Aguardando pagamento efetivo",
    "under_review": "Em análise",
    "paid": "Pagamento aprovado. Confirme a entrega dos pacotes.",
    "expired": "Cobrança expirada",
    "cancelled": "Cobrança cancelada",
    "refunded": "Pagamento estornado — conferir com o atendimento",
    "cancel_requested": "Cancelamento aguardando verificação",
    "delivered": "Pedido retirado com sucesso!",
}
METHODS = ("Pix AI", "Dinheiro")
FAST_MIN_CENTS = 400
FAST_MAX_CENTS = 100000
FAST_RANGE_MESSAGE = "Escolha Pix AI ou Dinheiro no ponto."


def schema(db, id_type, ensure):
    ensure(
        db,
        "plan_orders",
        {
            "local_method": "TEXT",
            "manual_paid_at": "TEXT",
            "creation_started_at": "TEXT",
            "cancel_state": "TEXT",
        },
    )
    ensure(
        db,
        "plan_admin_decisions",
        {
            "payment_method": "TEXT",
            "amount_cents": "INTEGER",
            "result": "TEXT",
            "http_status": "INTEGER",
            "http_method": "TEXT",
            "http_endpoint": "TEXT",
        },
    )
    ensure(
        db,
        "pickups",
        {
            "external_transaction_id": "TEXT",
            "payment_order_id": "TEXT",
            "payment_status": "TEXT",
        },
    )
    db.execute("""CREATE TABLE IF NOT EXISTS pickup_payment_orders (
        id TEXT PRIMARY KEY,client_id BIGINT NOT NULL REFERENCES clients(id),amount_cents INTEGER NOT NULL,
        state TEXT NOT NULL,transaction_id TEXT UNIQUE,callback_url TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,checked_at TEXT,check_after TEXT,expires_at TEXT,qr_text TEXT,
        paid_at TEXT,delivered_at TEXT,pickup_id BIGINT UNIQUE REFERENCES pickups(id),error_code TEXT,
        snapshot TEXT NOT NULL,quote_key TEXT NOT NULL,created_by TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS pickup_payment_packages (
        order_id TEXT NOT NULL REFERENCES pickup_payment_orders(id),package_id BIGINT NOT NULL REFERENCES packages(id),
        PRIMARY KEY(order_id,package_id))""")
    db.execute(
        """CREATE TABLE IF NOT EXISTS pickup_payment_reservations (
        package_id BIGINT PRIMARY KEY REFERENCES packages(id),order_id TEXT NOT NULL REFERENCES pickup_payment_orders(id))"""
    )
    ensure(db, "pickup_payment_orders", {"advance": "INTEGER NOT NULL DEFAULT 0"})
    ensure(
        db,
        "pickup_payment_orders",
        {
            "local_method": "TEXT NOT NULL DEFAULT ''",
            "policy_snapshot": "TEXT",
            "manual_confirmed_by": "TEXT",
        },
    )
    ensure(
        db,
        "plan_orders",
        {"policy_snapshot": "TEXT", "retry_of": "TEXT", "root_order_id": "TEXT"},
    )
    ensure(db, "settings", {"monthly_price_cents": "INTEGER NOT NULL DEFAULT 3390"})
    for table in ("plan_orders", "pickup_payment_orders"):
        ensure(db, table, {"archived_at": "TEXT", "archived_by": "TEXT"})
    ensure(db, "pickup_payment_orders", {"cancel_state": "TEXT"})
    ensure(db, "pickup_packages", {"prepaid_amount": "REAL"})
    db.execute("""CREATE TABLE IF NOT EXISTS package_prepayments (
        order_id TEXT NOT NULL REFERENCES pickup_payment_orders(id),
        package_id BIGINT NOT NULL REFERENCES packages(id), paid_cents INTEGER NOT NULL,
        settled_cents INTEGER NOT NULL, recorded_at TEXT NOT NULL,
        PRIMARY KEY(order_id,package_id))""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS package_prepayments_package ON package_prepayments(package_id)"
    )


def prepaid(db, package_id):
    row = db.execute(
        "SELECT COALESCE(SUM(paid_cents),0) paid,COALESCE(SUM(settled_cents),0) settled FROM package_prepayments WHERE package_id=?",
        (package_id,),
    ).fetchone()
    return row["paid"] / 100, row["settled"] / 100


def settle_advance(db, order, value):
    snap = json.loads(order["snapshot"])
    items = snap["packages"]
    weights = [max(0, round(p["total"] * 100)) for p in items]
    if not sum(weights):
        weights = [1] * len(items)
    total = sum(weights)
    portions = [order["amount_cents"] * w // total for w in weights]
    remaining = order["amount_cents"] - sum(portions)
    for i in sorted(
        range(len(items)), key=lambda i: -(order["amount_cents"] * weights[i] % total)
    )[:remaining]:
        portions[i] += 1
    for p, amount in zip(items, portions):
        db.execute(
            "INSERT INTO package_prepayments(order_id,package_id,paid_cents,settled_cents,recorded_at) VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING",
            (order["id"], p["id"], amount, round(p["total"] * 100), ps.iso(value)),
        )
    db.execute(
        "DELETE FROM pickup_payment_reservations WHERE order_id=?", (order["id"],)
    )


def quote(packages, form, fees):
    totals = [fees(p) for p in packages]
    package_total = Decimal(str(round(sum(t["total"] for t in totals), 2)))
    extra, reason = validate_surcharge(
        form.get("surcharge_amount", "0"), form.get("surcharge_reason", "")
    )
    kind, entered, discount, before, final = calculate_discount(
        float(package_total + extra),
        form.get("discount_amount", ""),
        form.get("discount_percent", ""),
    )
    return dict(
        package_total=float(package_total),
        surcharge_amount=float(extra),
        surcharge_reason=reason,
        discount_type=kind,
        discount_input=float(entered) if entered is not None else None,
        discount_amount=float(discount),
        original_total=float(before),
        final_total=float(final),
        packages=[
            dict(
                id=p["id"],
                public_id=p["public_id"],
                client_id=p["client_id"],
                category=p["category"],
                tracking=p["tracking"],
                notified_at=p["notified_at"],
                **t,
            )
            for p, t in zip(packages, totals)
        ],
    )


def key(snapshot):
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def assert_unreserved(db, ids, allowed=None):
    marks = ",".join("?" for _ in ids)
    if db.execute(
        "SELECT 1 FROM package_prepayments a JOIN pickup_payment_orders o ON o.id=a.order_id WHERE a.package_id IN ("
        + marks
        + ") AND (o.error_code IS NOT NULL OR o.state!='paid') LIMIT 1",
        tuple(ids),
    ).fetchone():
        raise ValueError(
            "Pagamento antecipado em análise. Solicite conferência ao administrador."
        )
    row = db.execute(
        f"SELECT order_id FROM pickup_payment_reservations WHERE package_id IN ({marks})"
        + (" AND order_id!=?" if allowed else "")
        + " LIMIT 1",
        tuple(ids) + ((allowed,) if allowed else ()),
    ).fetchone()
    if row:
        raise ValueError(
            "Há pagamento antecipado vinculado a estes pacotes. Consulte ou cancele a cobrança antes de mudar modalidade ou valores."
        )


def reserve(
    db,
    packages,
    snapshot,
    actor,
    value,
    settings,
    advance=False,
    reprice=None,
    selector=None,
):
    cents = round(snapshot["final_total"] * 100)
    if len({p["client_id"] for p in packages}) != 1:
        raise ValueError("Gere o pagamento antecipado separadamente para cada cliente.")
    ids = sorted(p["id"] for p in packages)
    marks = ",".join("?" for _ in ids)
    db.commit()
    db.begin_write()
    locked = db.execute(
        f"SELECT * FROM packages WHERE id IN ({marks}) ORDER BY id"
        + (" FOR UPDATE" if db.postgres else ""),
        tuple(ids),
    ).fetchall()
    if len(locked) != len(ids) or any(
        p["status"] != "aguardando_retirada" for p in locked
    ):
        raise ValueError("Há pacote indisponível para retirada.")
    old = db.execute(
        f"SELECT DISTINCT order_id FROM pickup_payment_reservations WHERE package_id IN ({marks})",
        tuple(ids),
    ).fetchall()
    if old:
        if len(old) == 1:
            order = dict(
                db.execute(
                    "SELECT * FROM pickup_payment_orders WHERE id=?",
                    (old[0]["order_id"],),
                ).fetchone()
            )
            same = (
                sorted(p["id"] for p in json.loads(order["snapshot"])["packages"])
                == ids
            )
            if order["quote_key"] == key(snapshot) or (
                same and (advance or order["advance"])
            ):
                if advance and not order["advance"]:
                    db.execute(
                        "UPDATE pickup_payment_orders SET advance=1 WHERE id=?",
                        (order["id"],),
                    )
                    order["advance"] = 1
                if (
                    advance
                    and order["state"] == "paid"
                    and not order["error_code"]
                    and not order["archived_at"]
                ):
                    settle_advance(db, order, value)
                db.commit()
                return order, False
        raise ValueError(
            "Pacotes já vinculados a outra cobrança. Mantenha os valores e a modalidade até a conclusão ou cancelamento."
        )
    if reprice:
        snapshot = reprice(locked)
        cents = round(snapshot["final_total"] * 100)
    selected = selector(cents) if selector else None
    if selector:
        if not selected or selected["method"] == "zero":
            raise ValueError(
                "Nada a pagar."
                if cents == 0
                else "Nenhuma modalidade disponível. Procure o ponto."
            )
    else:
        raise ValueError("Escolha Pix AI ou Dinheiro.")
    owner = db.execute(
        "SELECT * FROM clients WHERE id=?", (locked[0]["client_id"],)
    ).fetchone()
    snapshot = {**snapshot, "payer_document": re.sub(r"\D", "", owner["cpf"])}
    oid = str(uuid.uuid4())
    callback = "local:" + oid
    db.execute(
        "INSERT INTO pickup_payment_orders(id,client_id,amount_cents,state,callback_url,created_at,snapshot,quote_key,created_by) VALUES(?,?,?,'creating',?,?,?,?,?)",
        (
            oid,
            owner["id"],
            cents,
            callback,
            ps.iso(value),
            json.dumps(snapshot, ensure_ascii=False),
            key(snapshot),
            actor,
        ),
    )
    db.execute(
        "UPDATE pickup_payment_orders SET advance=? WHERE id=?", (int(advance), oid)
    )
    if selected:
        method = selected["method"]
        db.execute(
            "UPDATE pickup_payment_orders SET local_method=?,policy_snapshot=?,state=?,qr_text=? WHERE id=?",
            (
                method,
                json.dumps(selected, ensure_ascii=False),
                "pending",
                policies.local_code(db, method, cents),
                oid,
            ),
        )
    for pid in ids:
        db.execute(
            "INSERT INTO pickup_payment_packages(order_id,package_id) VALUES(?,?)",
            (oid, pid),
        )
        db.execute(
            "INSERT INTO pickup_payment_reservations(package_id,order_id) VALUES(?,?)",
            (pid, oid),
        )
    db.commit()
    return (
        dict(
            db.execute(
                "SELECT * FROM pickup_payment_orders WHERE id=?", (oid,)
            ).fetchone()
        ),
        True,
    )


def install(
    app, db, staff_required, current_staff, now, fees, qr_data, client_required
):
    bp = Blueprint("pickup_payments", __name__)

    def permission():
        staff = current_staff()
        if staff and not staff["can_pickups"]:
            abort(403)

    def order(oid):
        row = (
            db()
            .execute("SELECT * FROM pickup_payment_orders WHERE id=?", (oid,))
            .fetchone()
        )
        if not row:
            abort(404)
        return row

    @bp.post("/painel/retirada/cobrancas")
    @staff_required
    def create():
        permission()
        return create_charge()

    def create_charge(client_id=None, advance=False):
        try:
            ids = sorted(set(int(x) for x in request.form.getlist("package_ids")))
            if not ids or len(ids) > 150:
                raise ValueError("Selecione de 1 a 150 pacotes.")
            rows = (
                db()
                .execute(
                    "SELECT * FROM packages WHERE id IN ("
                    + ",".join("?" for _ in ids)
                    + ") ORDER BY id",
                    tuple(ids),
                )
                .fetchall()
            )
            if len(rows) != len(ids):
                raise ValueError("Pacote não encontrado.")
            if client_id is not None and any(p["client_id"] != client_id for p in rows):
                abort(404)
            form = {} if client_id is not None else request.form
            snapshot = quote(rows, form, fees)
            cfg = LocalSettings.from_config(app.config)
            owner = (
                db()
                .execute("SELECT * FROM clients WHERE id=?", (rows[0]["client_id"],))
                .fetchone()
            )
            snapshot["payer_document"] = re.sub(r"\D", "", owner["cpf"])

            def reprice(locked):
                fresh = {
                    **quote(locked, form, fees),
                    "payer_document": snapshot["payer_document"],
                }
                expected = (
                    request.form.get("expected_total")
                    if client_id is not None
                    else None
                )
                if expected is not None and expected != str(
                    round(fresh["final_total"] * 100)
                ):
                    raise ValueError(
                        "O saldo mudou. Volte aos pacotes e confira o total novamente."
                    )
                return fresh

            item, new = reserve(
                db(),
                rows,
                snapshot,
                (
                    ("client:" + str(client_id))
                    if client_id is not None
                    else str(session.get("staff_user_id") or session.get("staff"))
                ),
                now(),
                cfg,
                advance=advance,
                reprice=reprice,
                selector=(
                    (
                        lambda cents: policies.select(
                            db(), app, "advance", cents, client_id=rows[0]["client_id"]
                        )
                    )
                    if advance
                    else (
                        lambda cents: policies.select(
                            db(), app, "advance", cents, client_id=rows[0]["client_id"]
                        )
                    )
                ),
            )
            return {
                "id": item["id"],
                "url": url_for(
                    (
                        "pickup_payments.client_payment"
                        if client_id is not None
                        else "pickup_payments.payment"
                    ),
                    oid=item["id"],
                ),
            }
        except (ValueError, PaymentError, DatabaseIntegrityError) as e:
            db().rollback()
            return {
                "error": (
                    str(e)
                    if not isinstance(e, DatabaseIntegrityError)
                    else "A seleção mudou. Consulte os pacotes antes de gerar outra cobrança."
                )
            }, 400

    @bp.post("/painel/retirada/antecipar")
    @staff_required
    def staff_advance():
        permission()
        return create_charge(advance=True)

    @bp.post("/cliente/pacotes/pagar/gerar")
    @client_required
    def client_create():
        return create_charge(client_id=session["client_id"], advance=True)

    def advance_label(item):
        if item["state"] == "paid" and not item["error_code"]:
            waiting = (
                db()
                .execute(
                    "SELECT 1 FROM packages WHERE status!='retirado' AND id IN (SELECT package_id FROM pickup_payment_packages WHERE order_id=?) LIMIT 1",
                    (item["id"],),
                )
                .fetchone()
            )
            return (
                "Pago antecipadamente · Aguardando retirada"
                if waiting
                else "Pago antecipadamente · Retirada concluída"
            )
        return (
            "Verificando cobrança"
            if item["state"] in ("creating", "uncertain", "cancel_requested")
            or (item["error_code"] and item["state"] not in ("paid", "delivered"))
            else LABELS.get(item["state"], "Em consulta")
        )

    def client_order(oid):
        item = order(oid)
        if item["client_id"] != session["client_id"]:
            abort(404)
        return item

    @bp.route("/cliente/pacotes/pagamentos/<oid>", methods=["GET", "POST"])
    @client_required
    def client_payment(oid):
        client_order(oid)
        item = client_order(oid)
        return render_template(
            "advance_payment.html",
            order=item,
            snapshot=json.loads(item["snapshot"]),
            qr=(qr_data(code) if (code := ps.payment_code(item, now())) else None),
            labels=LABELS,
            advance_label=advance_label(item),
            choice=(
                json.loads(item["policy_snapshot"])
                if item["policy_snapshot"]
                else {"title": item["local_method"], "text": ""}
            ),
        )

    @bp.get("/cliente/pacotes/pagamentos/<oid>/status")
    @client_required
    def client_payment_status(oid):
        client_order(oid)
        item = client_order(oid)
        return {
            "state": item["state"],
            "label": advance_label(item),
            "done": item["state"]
            not in ("creating", "uncertain", "pending", "approved", "under_review"),
            "pix_available": bool(ps.payment_code(item, now())),
        }

    @bp.post("/cliente/pacotes/pagar")
    @client_required
    def client_review():
        try:
            ids = sorted(set(int(x) for x in request.form.getlist("package_ids")))
            if not ids or len(ids) > 150:
                raise ValueError("Selecione de 1 a 150 pacotes.")
            rows = (
                db()
                .execute(
                    "SELECT * FROM packages WHERE client_id=? AND status='aguardando_retirada' AND id IN ("
                    + ",".join("?" for _ in ids)
                    + ") ORDER BY id",
                    (session["client_id"], *ids),
                )
                .fetchall()
            )
            if len(rows) != len(ids):
                abort(404)
            held = (
                db()
                .execute(
                    "SELECT DISTINCT order_id FROM pickup_payment_reservations WHERE package_id IN ("
                    + ",".join("?" for _ in ids)
                    + ")",
                    tuple(ids),
                )
                .fetchall()
            )
            if held:
                if len(held) == 1:
                    old = client_order(held[0]["order_id"])
                    if (
                        sorted(p["id"] for p in json.loads(old["snapshot"])["packages"])
                        == ids
                    ):
                        db().commit()
                        db().begin_write()
                        db().execute(
                            "SELECT id FROM packages WHERE id IN ("
                            + ",".join("?" for _ in ids)
                            + ") ORDER BY id"
                            + (" FOR UPDATE" if db().postgres else ""),
                            tuple(ids),
                        ).fetchall()
                        db().execute(
                            "UPDATE pickup_payment_orders SET advance=1 WHERE id=?",
                            (old["id"],),
                        )
                        current = order(old["id"])
                        if (
                            current["state"] == "paid"
                            and not current["error_code"]
                            and not current["archived_at"]
                        ):
                            settle_advance(db(), current, now())
                        db().commit()
                        return redirect(
                            url_for("pickup_payments.client_payment", oid=old["id"])
                        )
                raise ValueError(
                    "Pacotes já vinculados a outro Pix pendente. Consulte a cobrança existente."
                )
            snap = quote(rows, {}, fees)
            return render_template(
                "advance_review.html",
                snapshot=snap,
                choice=policies.select(
                    db(),
                    app,
                    "advance",
                    round(snap["final_total"] * 100),
                    client_id=session["client_id"],
                ),
                allowed=snap["final_total"] > 0
                and policies.select(
                    db(),
                    app,
                    "advance",
                    round(snap["final_total"] * 100),
                    client_id=session["client_id"],
                )
                is not None,
                range_message=FAST_RANGE_MESSAGE,
            )
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("client_area", aba="pacotes"))

    @bp.post("/painel/retirada/cobrancas/<oid>/confirmar-manual")
    @staff_required
    def confirm_manual(oid):
        permission()
        item = order(oid)
        if item["local_method"] not in ("Pix AI", "Dinheiro"):
            abort(403)
        if request.form.get("payment_confirmed") != "yes":
            abort(400)
        db().commit()
        db().begin_write()
        db().execute(
            "SELECT id FROM packages WHERE id IN (SELECT package_id FROM pickup_payment_packages WHERE order_id=?) ORDER BY id"
            + (" FOR UPDATE" if db().postgres else ""),
            (oid,),
        ).fetchall()
        item = (
            db()
            .execute(
                "SELECT * FROM pickup_payment_orders WHERE id=?"
                + (" FOR UPDATE" if db().postgres else ""),
                (oid,),
            )
            .fetchone()
        )
        if item["state"] == "paid":
            db().commit()
            return redirect(url_for("pickup_payments.payment", oid=oid))
        if item["state"] != "pending":
            db().rollback()
            abort(409)
        db().execute(
            "UPDATE pickup_payment_orders SET state='paid',paid_at=?,manual_confirmed_by=? WHERE id=?",
            (
                ps.iso(now()),
                str(session.get("staff_user_id") or session.get("staff")),
                oid,
            ),
        )
        settle_advance(db(), order(oid), now())
        db().commit()
        flash("Pagamento conferido pela equipe. Saldo quitado.", "success")
        return redirect(url_for("pickup_payments.payment", oid=oid))

    @bp.get("/painel/retirada/cobrancas/<oid>")
    @staff_required
    def payment(oid):
        permission()
        order(oid)
        item = order(oid)
        snap = json.loads(item["snapshot"])
        codes = snap["packages"]
        return render_template(
            "pickup_payment.html",
            order=item,
            snapshot=snap,
            packages=codes,
            labels=LABELS,
            qr=(qr_data(code) if (code := ps.payment_code(item, now())) else None),
        )

    @bp.get("/painel/retirada/cobrancas/<oid>/status")
    @staff_required
    def status(oid):
        permission()
        order(oid)
        item = order(oid)
        return {
            "state": item["state"],
            "label": LABELS.get(item["state"], "Em consulta"),
            "done": item["state"]
            not in ("creating", "uncertain", "pending", "approved", "under_review"),
            "pix_available": bool(ps.payment_code(item, now())),
        }

    @bp.post("/painel/retirada/cobrancas/<oid>/consultar")
    @staff_required
    def check(oid):
        permission()
        order(oid)
        return redirect(url_for("pickup_payments.payment", oid=oid))

    @bp.get("/painel/retirada/cobrancas")
    @staff_required
    def charges():
        permission()
        archived = request.args.get("arquivadas") == "1"
        try:
            page = max(1, int(request.args.get("page", "1")))
        except ValueError:
            abort(400)
        rows = (
            db()
            .execute(
                "SELECT o.*,c.name,c.public_id FROM pickup_payment_orders o JOIN clients c ON c.id=o.client_id WHERE o.local_method IN ('Pix AI','Dinheiro') AND o.archived_at IS "
                + ("NOT NULL" if archived else "NULL")
                + " ORDER BY o.created_at DESC LIMIT 101 OFFSET ?",
                ((page - 1) * 100,),
            )
            .fetchall()
        )
        return render_template(
            "pickup_charges.html",
            orders=rows[:100],
            page=page,
            has_next=len(rows) > 100,
            archived=archived,
            labels=LABELS,
        )

    @bp.post("/painel/retirada/cobrancas/<oid>/cancelar")
    @staff_required
    def cancel(oid):
        permission()
        db().commit()
        db().begin_write()
        item = (
            db()
            .execute(
                "SELECT * FROM pickup_payment_orders WHERE id=?"
                + (" FOR UPDATE" if db().postgres else ""),
                (oid,),
            )
            .fetchone()
        )
        if not item:
            abort(404)
        if item["paid_at"] or item["state"] in ("paid", "delivered"):
            db().rollback()
            flash("Pagamento confirmado: solicite análise ao administrador.", "error")
        else:
            db().execute(
                "UPDATE pickup_payment_orders SET state='cancelled',qr_text=NULL WHERE id=?",
                (oid,),
            )
            db().execute(
                "DELETE FROM pickup_payment_reservations WHERE order_id=?", (oid,)
            )
            db().commit()
            flash("Solicitação não paga cancelada.", "success")
        return redirect(url_for("pickup_payments.payment", oid=oid))

    app.register_blueprint(bp)
