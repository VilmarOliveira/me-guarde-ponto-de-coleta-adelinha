"""Pickup-specific Fast charges. Payment never confirms physical delivery."""

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
from fastflow_gateway import (
    Settings, GatewayError, transaction_id, log_pix_failure, pix_creation_details,
)
from database import DatabaseIntegrityError
import plan_service as ps
import billing_archive
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
METHODS = ("Pix AI", "Pix Fast", "Dinheiro")
FAST_MIN_CENTS = 400
FAST_MAX_CENTS = 100000
FAST_RANGE_MESSAGE = "Pix Fast disponível para cobranças de R$ 4,00 a R$ 1.000,00. Escolha Pix AI ou Dinheiro."


def fast_available(total):
    return FAST_MIN_CENTS <= round(total * 100) <= FAST_MAX_CENTS


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
            "local_method": "TEXT NOT NULL DEFAULT 'Pix Fast'",
            "policy_snapshot": "TEXT",
            "manual_confirmed_by": "TEXT",
        },
    )
    ensure(db, "plan_orders", {"policy_snapshot": "TEXT", "retry_of": "TEXT", "root_order_id": "TEXT"})
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
    db.execute(f"""CREATE TABLE IF NOT EXISTS pickup_payment_events (
        id {id_type},order_id TEXT NOT NULL REFERENCES pickup_payment_orders(id),transaction_id TEXT NOT NULL,
        event_key TEXT NOT NULL UNIQUE,event_type TEXT NOT NULL,received_at TEXT NOT NULL,
        processed_at TEXT,outcome TEXT NOT NULL,reason TEXT)""")


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


def amount_ok(value, cents):
    try:
        return Decimal(str(value)).is_finite() and Decimal(str(value)) * 100 == cents
    except (InvalidOperation, ValueError, TypeError):
        return False


def validate(order, data):
    if dict(order).get("local_method", "Pix Fast") != "Pix Fast":
        raise GatewayError(code="wrong_payment_method")
    if not isinstance(data, dict):
        raise GatewayError(code="invalid_response")
    tid = transaction_id(data.get("id", data.get("transaction_id")))
    if order["transaction_id"] and tid != order["transaction_id"]:
        raise GatewayError(code="identity_mismatch")
    if data.get("payment_provider") != "fastflow" or not amount_ok(
        data.get("amount"), order["amount_cents"]
    ):
        raise GatewayError(code="amount_provider_mismatch")
    if (
        data.get("notification_url") != order["callback_url"]
        or data.get("payment_method", "pix") != "pix"
    ):
        raise GatewayError(code="identity_mismatch")
    user = data.get("user") or {}
    if not isinstance(user, dict):
        raise GatewayError(code="invalid_response")
    document = user.get("cpf_cnpj")
    if (
        document
        and re.sub(r"\D", "", str(document))
        != json.loads(order["snapshot"])["payer_document"]
    ):
        raise GatewayError(code="identity_mismatch")
    return tid


def apply(db, oid, data, value):
    row = db.execute(
        "SELECT * FROM pickup_payment_orders WHERE id=?", (oid,)
    ).fetchone()
    if not row:
        raise GatewayError(code="unknown_order")
    tid = validate(row, data)
    state = data.get("status")
    if state not in (
        "pending",
        "approved",
        "under_review",
        "paid",
        "cancelled",
        "expired",
        "refunded",
    ):
        raise GatewayError(code="unknown_status")
    db.commit()
    db.begin_write()
    db.execute(
        "SELECT id FROM packages WHERE id IN (SELECT package_id FROM pickup_payment_packages WHERE order_id=?) ORDER BY id"
        + (" FOR UPDATE" if db.postgres else ""),
        (oid,),
    ).fetchall()
    order = db.execute(
        "SELECT * FROM pickup_payment_orders WHERE id=?"
        + (" FOR UPDATE" if db.postgres else ""),
        (oid,),
    ).fetchone()
    validate(order, data)
    if db.execute(
        "SELECT 1 FROM plan_orders WHERE transaction_id=?", (tid,)
    ).fetchone():
        raise GatewayError(code="identity_mismatch")
    other = db.execute(
        "SELECT 1 FROM pickup_payment_orders WHERE transaction_id=? AND id!=?",
        (tid, oid),
    ).fetchone()
    if other:
        raise GatewayError(code="identity_mismatch")
    db.execute(
        "UPDATE pickup_payment_orders SET transaction_id=?,checked_at=?,check_after=?,qr_text=COALESCE(?,qr_text),expires_at=COALESCE(?,expires_at),error_code=CASE WHEN error_code IN ('late_payment_review','archived_payment_review') THEN error_code ELSE NULL END WHERE id=?",
        (
            tid,
            ps.iso(value),
            ps.iso(value + timedelta(seconds=20)),
            (
                data.get("qr_code_text")
                if isinstance(data.get("qr_code_text"), str)
                and len(data["qr_code_text"]) <= 4096
                else None
            ),
            ps.safe_time(data.get("qr_code_expires_at")),
            oid,
        ),
    )
    if order["archived_at"] and state == "paid" and not order["paid_at"]:
        db.execute(
            "UPDATE pickup_payment_orders SET state='paid',paid_at=?,error_code='archived_payment_review' WHERE id=?",
            (ps.iso(value), oid),
        )
        db.commit()
        return
    if order["delivered_at"]:
        if state in ("refunded", "under_review"):
            db.execute(
                "UPDATE pickup_payment_orders SET error_code='delivered_payment_review' WHERE id=?",
                (oid,),
            )
    elif (
        order["state"] in ("cancel_requested", "cancelled", "expired", "refunded")
        or order["cancel_state"] == "external_pending"
    ):
        if state == "paid":
            db.execute(
                "UPDATE pickup_payment_orders SET state=CASE WHEN cancel_state='external_pending' THEN 'paid' ELSE state END,paid_at=COALESCE(paid_at,?),error_code='late_payment_review' WHERE id=?",
                (ps.iso(value), oid),
            )
        elif state in ("cancelled", "expired", "refunded"):
            db.execute(
                "UPDATE pickup_payment_orders SET state=? WHERE id=?", (state, oid)
            )
            db.execute(
                "DELETE FROM pickup_payment_reservations WHERE order_id=?", (oid,)
            )
    elif state == "paid":
        db.execute(
            "UPDATE pickup_payment_orders SET state='paid',paid_at=COALESCE(paid_at,?) WHERE id=?",
            (ps.iso(value), oid),
        )
    elif order["state"] == "paid":
        if state in ("refunded", "under_review"):
            db.execute(
                "UPDATE pickup_payment_orders SET state=?,error_code='payment_review' WHERE id=?",
                (state, oid),
            )
    else:
        db.execute("UPDATE pickup_payment_orders SET state=? WHERE id=?", (state, oid))
        if state in ("cancelled", "expired", "refunded"):
            db.execute(
                "DELETE FROM pickup_payment_reservations WHERE order_id=?", (oid,)
            )
    current = db.execute(
        "SELECT * FROM pickup_payment_orders WHERE id=?", (oid,)
    ).fetchone()
    if (
        current["advance"]
        and current["state"] == "paid"
        and not current["error_code"]
        and not current["archived_at"]
    ):
        settle_advance(db, current, value)
    db.commit()


def refresh(db, app, oid, value):
    row = db.execute(
        "SELECT * FROM pickup_payment_orders WHERE id=?", (oid,)
    ).fetchone()
    if not row:
        return False
    if row["local_method"] != "Pix Fast" or not Settings.from_config(app.config).ready:
        return False
    order = dict(row)
    if order["check_after"] and ps.parse(order["check_after"]) > value:
        return False
    lease = db.execute(
        "UPDATE pickup_payment_orders SET check_after=? WHERE id=? AND (check_after IS NULL OR check_after<=?) RETURNING id",
        (ps.iso(value + timedelta(seconds=20)), oid, ps.iso(value)),
    ).fetchone()
    db.commit()
    if not lease:
        return False
    import plan_routes

    try:
        api = plan_routes.gateway(app)
        data = (
            api.get(order["transaction_id"])
            if order["transaction_id"]
            else api.find(order)
        )
        if data is None:
            return False
        apply(db, oid, data, value)
        return True
    except (GatewayError, DatabaseIntegrityError) as e:
        db.rollback()
        db.execute(
            "UPDATE pickup_payment_orders SET error_code=?,check_after=? WHERE id=?",
            (
                getattr(e, "code", "database_conflict"),
                ps.iso(value + timedelta(minutes=2)),
                oid,
            ),
        )
        db.commit()
        return False


def refresh_ended_reservations(db, app, ids, value):
    marks = ",".join("?" for _ in ids)
    orders = db.execute(
        f"""SELECT DISTINCT o.* FROM pickup_payment_orders o
        JOIN pickup_payment_reservations r ON r.order_id=o.id
        WHERE r.package_id IN ({marks}) AND o.local_method='Pix Fast'
        AND o.state='pending'""",
        tuple(ids),
    ).fetchall()
    for item in orders:
        if ps.fast_pix_window_ended(item, value):
            refresh(db, app, item["id"], value)


def webhook(db, app, rid, tid, event, raw, value):
    row = db.execute(
        "SELECT * FROM pickup_payment_orders WHERE "
        + ("id=?" if rid else "transaction_id=?"),
        (rid or tid,),
    ).fetchone()
    if not row:
        return "", 202
    if row["transaction_id"] and row["transaction_id"] != tid:
        return "", 409
    event_key = hashlib.sha256((row["id"] + ":" + tid + ":").encode() + raw).hexdigest()
    done = db.execute(
        "SELECT 1 FROM pickup_payment_events WHERE event_key=? AND outcome='processed'",
        (event_key,),
    ).fetchone()
    if done:
        return "", 200
    db.execute(
        "INSERT INTO pickup_payment_events(order_id,transaction_id,event_key,event_type,received_at,outcome) VALUES(?,?,?,?,?,'pending') ON CONFLICT DO NOTHING",
        (row["id"], tid, event_key, event, ps.iso(value)),
    )
    db.commit()
    import plan_routes

    try:
        fresh = plan_routes.gateway(app).get(tid)
        apply(db, row["id"], fresh, value)
        if event == "transaction.paid" and fresh["status"] != "paid":
            return "", 202
        db.execute(
            "UPDATE pickup_payment_events SET outcome='processed',processed_at=? WHERE event_key=?",
            (ps.iso(value), event_key),
        )
        db.commit()
        return "", 200
    except (GatewayError, DatabaseIntegrityError) as e:
        db.rollback()
        db.execute(
            "UPDATE pickup_payment_events SET reason=? WHERE event_key=?",
            (getattr(e, "code", "database_conflict"), event_key),
        )
        db.commit()
        return "", 202


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
            "Há Pix Fast vinculado a estes pacotes. Consulte ou cancele a cobrança antes de mudar modalidade ou valores."
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
        raise ValueError(
            "Gere Pix Fast separadamente para cada cliente. Pix AI e Dinheiro mantêm a seleção conjunta."
        )
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
    elif not fast_available(snapshot["final_total"]):
        raise ValueError(FAST_RANGE_MESSAGE)
    is_fast = not selected or selected["method"] == "Pix Fast"
    if is_fast:
        db.execute(
            "SELECT id FROM plan_gateway_lock WHERE id=1"
            + (" FOR UPDATE" if db.postgres else "")
        ).fetchone()
        count = (
            db.execute(
                "SELECT COUNT(*) n FROM pickup_payment_orders WHERE created_at>?",
                (ps.iso(value - timedelta(minutes=1)),),
            ).fetchone()["n"]
            + db.execute(
                "SELECT COUNT(*) n FROM plan_orders WHERE method!='administrative' AND created_at>?",
                (ps.iso(value - timedelta(minutes=1)),),
            ).fetchone()["n"]
        )
        if count >= 4:
            raise ValueError("Aguarde um minuto antes de gerar outra cobrança Fast.")
    owner = db.execute(
        "SELECT * FROM clients WHERE id=?", (locked[0]["client_id"],)
    ).fetchone()
    snapshot = {**snapshot, "payer_document": re.sub(r"\D", "", owner["cpf"])}
    oid = str(uuid.uuid4())
    callback = (
        settings.origin + "/webhooks/fastflow/retiradas/" + oid
        if is_fast
        else "local:" + oid
    )
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
                "creating" if method == "Pix Fast" else "pending",
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


def final_quote(db, oid, ids, form):
    order = db.execute(
        "SELECT * FROM pickup_payment_orders WHERE id=?"
        + (" FOR UPDATE" if db.postgres else ""),
        (oid,),
    ).fetchone()
    if (
        not order
        or order["state"] != "paid"
        or not order["paid_at"]
        or order["delivered_at"]
        or order["error_code"]
    ):
        raise ValueError(
            "Confirme o pagamento Pix Fast no servidor antes da entrega física."
        )
    snap = json.loads(order["snapshot"])
    if sorted(ids) != sorted(p["id"] for p in snap["packages"]):
        raise ValueError("A cobrança não corresponde aos pacotes selecionados.")
    # Normalize adjustment fields, without silently adding charges after payment.
    extra, reason = validate_surcharge(
        form.get("surcharge_amount", "0"), form.get("surcharge_reason", "")
    )
    kind, entered, _, _, _ = calculate_discount(
        snap["package_total"] + float(extra),
        form.get("discount_amount", ""),
        form.get("discount_percent", ""),
    )
    if (
        float(extra) != snap["surcharge_amount"]
        or reason != snap["surcharge_reason"]
        or kind != snap["discount_type"]
        or (float(entered) if entered is not None else None) != snap["discount_input"]
    ):
        raise ValueError(
            "Os ajustes diferem do Pix Fast pago. Não altere valores de uma cobrança existente."
        )
    assert_unreserved(db, ids, oid)
    held = db.execute(
        "SELECT COUNT(*) n FROM pickup_payment_reservations WHERE order_id=?", (oid,)
    ).fetchone()["n"]
    if held != len(ids):
        raise ValueError(
            "A reserva dos pacotes mudou. Solicite conferência antes da entrega."
        )
    return order, snap


def reconcile(db, app, value):
    events = db.execute(
        "SELECT * FROM pickup_payment_events WHERE outcome='pending' ORDER BY id LIMIT 3"
    ).fetchall()
    for e in events:
        import plan_routes

        try:
            fresh = plan_routes.gateway(app).get(e["transaction_id"])
            apply(db, e["order_id"], fresh, value)
            if e["event_type"] == "transaction.paid" and fresh["status"] != "paid":
                continue
            db.execute(
                "UPDATE pickup_payment_events SET outcome='processed',processed_at=? WHERE id=?",
                (ps.iso(value), e["id"]),
            )
            db.commit()
        except GatewayError:
            db.rollback()
    for row in db.execute(
        "SELECT id FROM pickup_payment_orders WHERE local_method='Pix Fast' AND ((state IN ('creating','uncertain','pending','approved','under_review','cancel_requested') AND (check_after IS NULL OR check_after<=?)) OR (state IN ('paid','expired','cancelled','delivered') AND transaction_id IS NOT NULL AND checked_at<?)) ORDER BY COALESCE(checked_at,''),created_at LIMIT 5",
        (ps.iso(value), ps.iso(value - timedelta(hours=6))),
    ).fetchall():
        refresh(db, app, row["id"], value)


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

    @bp.post("/painel/retirada/pix-fast")
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
            cfg = Settings.from_config(app.config)
            if not cfg.ready and not advance:
                raise ValueError(
                    "Pix Fast não configurado. Utilize Pix AI ou Dinheiro."
                )
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

            refresh_ended_reservations(db(), app, ids, now())
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
                    else None
                ),
            )
            if new and item["local_method"] == "Pix Fast":
                import plan_routes

                api = plan_routes.gateway(app)
                created = False
                try:
                    data = api.create({**item, "method": "pix"}, owner)
                    created = True
                    tid = transaction_id(data.get("id"))
                    # Keep the POST identifier before a detail GET, without marking paid.
                    if data.get("payment_provider") != "fastflow" or not amount_ok(
                        data.get("amount"), item["amount_cents"]
                    ):
                        raise GatewayError(code="amount_provider_mismatch")
                    if (
                        db()
                        .execute(
                            "SELECT 1 FROM plan_orders WHERE transaction_id=?", (tid,)
                        )
                        .fetchone()
                    ):
                        raise GatewayError(code="identity_mismatch")
                    db().execute(
                        "UPDATE pickup_payment_orders SET transaction_id=? WHERE id=?",
                        (tid, item["id"]),
                    )
                    db().commit()
                    details = pix_creation_details(data, api.get(tid))
                    apply(db(), item["id"], details, now())
                except (GatewayError, DatabaseIntegrityError) as e:
                    db().rollback()
                    log_pix_failure(app.logger, item["id"], e)
                    state = (
                        "uncertain" if created or getattr(e, "uncertain", False)
                        else "failed"
                    )
                    db().execute(
                        "UPDATE pickup_payment_orders SET state=?,error_code=? WHERE id=? AND state='creating'",
                        (state, getattr(e, "code", "database_conflict"), item["id"]),
                    )
                    if state == "failed":
                        db().execute(
                            "DELETE FROM pickup_payment_reservations WHERE order_id=?",
                            (item["id"],),
                        )
                    db().commit()
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
        except (ValueError, GatewayError, DatabaseIntegrityError) as e:
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
        refresh(db(), app, oid, now())
        item = client_order(oid)
        return render_template(
            "advance_payment.html",
            order=item,
            snapshot=json.loads(item["snapshot"]),
            qr=(
                qr_data(code) if (code := ps.payment_code(item, now())) else None
            ),
            pix_window_ended=ps.fast_pix_window_ended(item, now()),
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
        refresh(db(), app, oid, now())
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
            refresh_ended_reservations(db(), app, ids, now())
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

    @bp.post("/painel/retirada/pix-fast/<oid>/confirmar-manual")
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

    @bp.get("/painel/retirada/pix-fast/<oid>")
    @staff_required
    def payment(oid):
        permission()
        order(oid)
        refresh(db(), app, oid, now())
        item = order(oid)
        snap = json.loads(item["snapshot"])
        codes = snap["packages"]
        return render_template(
            "pickup_payment.html",
            order=item,
            snapshot=snap,
            packages=codes,
            labels=LABELS,
            qr=(
                qr_data(code) if (code := ps.payment_code(item, now())) else None
            ),
            pix_window_ended=ps.fast_pix_window_ended(item, now()),
        )

    @bp.get("/painel/retirada/pix-fast/<oid>/status")
    @staff_required
    def status(oid):
        permission()
        order(oid)
        refresh(db(), app, oid, now())
        item = order(oid)
        return {
            "state": item["state"],
            "label": LABELS.get(item["state"], "Em consulta"),
            "done": item["state"]
            not in ("creating", "uncertain", "pending", "approved", "under_review"),
            "pix_available": bool(ps.payment_code(item, now())),
        }

    @bp.post("/painel/retirada/pix-fast/<oid>/consultar")
    @staff_required
    def check(oid):
        permission()
        order(oid)
        refresh(db(), app, oid, now())
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
                "SELECT o.*,c.name,c.public_id FROM pickup_payment_orders o JOIN clients c ON c.id=o.client_id WHERE COALESCE(o.created_by,'')!='historical_entitlement' AND o.archived_at IS "
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

    @bp.post("/painel/retirada/pix-fast/<oid>/arquivar")
    @staff_required
    def archive(oid):
        permission()
        try:
            billing_archive.archive(
                db(),
                "pickup_payment_orders",
                oid,
                str(session.get("staff_user_id") or session.get("staff")),
                ps.iso(now()),
            )
            flash(
                "Cobrança arquivada. O acompanhamento do pagamento continua.", "success"
            )
        except ValueError as exc:
            flash(str(exc), "error")
        return redirect(url_for("pickup_payments.charges"))

    @bp.post("/painel/retirada/pix-fast/<oid>/cancelar")
    @staff_required
    def cancel(oid):
        permission()
        item = order(oid)
        if item["state"] == "paid" or item["paid_at"]:
            flash("Pagamento confirmado: solicite análise ao administrador.", "error")
            return redirect(url_for("pickup_payments.payment", oid=oid))
        if item["local_method"] != "Pix Fast":
            db().commit()
            db().begin_write()
            locked = (
                db()
                .execute(
                    "SELECT * FROM pickup_payment_orders WHERE id=?"
                    + (" FOR UPDATE" if db().postgres else ""),
                    (oid,),
                )
                .fetchone()
            )
            if locked["paid_at"]:
                db().rollback()
                abort(409)
            db().execute(
                "UPDATE pickup_payment_orders SET state='cancelled' WHERE id=?", (oid,)
            )
            db().execute(
                "DELETE FROM pickup_payment_reservations WHERE order_id=?", (oid,)
            )
            db().commit()
            return redirect(url_for("pickup_payments.payment", oid=oid))
        import plan_routes

        try:
            api = plan_routes.gateway(app)
            data = (
                api.get(item["transaction_id"])
                if item["transaction_id"]
                else api.find(dict(item))
            )
            if data:
                validate(item, data)
                if data["status"] not in ("paid", "cancelled", "expired", "refunded"):
                    api.cancel(data["id"])
                    data = api.get(data["id"])
                apply(db(), oid, data, now())
        except GatewayError as exc:
            db().rollback()
            db().execute(
                "UPDATE pickup_payment_orders SET cancel_state='external_pending',error_code='cancel_awaiting_verification',check_after=NULL WHERE id=?",
                (oid,),
            )
            db().commit()
            flash(
                "Não foi possível confirmar o cancelamento. O status da cobrança foi mantido. "
                + str(exc),
                "error",
            )
            return redirect(url_for("pickup_payments.payment", oid=oid))
        current = order(oid)
        flash(
            (
                "Cancelada"
                if current["state"] == "cancelled"
                else "O cancelamento não foi confirmado. Confira a situação atual antes de tentar novamente."
            ),
            "info",
        )
        return redirect(url_for("pickup_payments.payment", oid=oid))

    app.register_blueprint(bp)
