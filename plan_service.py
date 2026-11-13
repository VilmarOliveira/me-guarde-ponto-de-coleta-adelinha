"""Append-only financial evidence and monthly credit entitlements.

Network work is performed outside write locks. All activation and credit changes
are serialized per client and committed atomically. Existing package fees are
never recomputed from the customer's current plan.
"""

import re
import plan_pricing as pricing
import calendar
import uuid
from datetime import datetime, timedelta
from core import TZ
from fastflow_gateway import AMOUNT_CENTS, money_matches, transaction_id, GatewayError

PLAN_RULES_VERSION = "ME-GUARDE-MAIS-2026-11-v1"
PLAN_RULES = "Plano ME GUARDE Mais: R$ 33,90 por ciclo mensal, 10 créditos, pequeno 1 crédito e grande 2 créditos. Créditos não acumulam. Excedentes e atraso são cobrados à parte. Sem débito automático. Renovação antecipada inicia após o ciclo vigente; após vencimento, um novo ciclo inicia com a confirmação do pagamento. A cobertura de pacotes recebidos é preservada."
CREDITS_PER_CYCLE = 10
SMALL_CREDIT_COST = 1
LARGE_CREDIT_COST = 2

OPEN_STATES = ("creating", "uncertain", "pending", "approved", "under_review")


def public_offer(db=None):
    price = pricing.current(db) if db is not None else AMOUNT_CENTS
    from core import (
        SMALL_PACKAGE_PRICE,
        LARGE_PACKAGE_PRICE,
        SMALL_PACKAGE_MAX_SUM,
        LARGE_PACKAGE_MAX_SUM,
        SMALL_PACKAGE_MAX_WEIGHT,
        LARGE_PACKAGE_MAX_WEIGHT,
    )

    small_count = CREDITS_PER_CYCLE // SMALL_CREDIT_COST
    large_count = CREDITS_PER_CYCLE // LARGE_CREDIT_COST
    avulso_cents = max(
        round(SMALL_PACKAGE_PRICE * 100) * small_count,
        round(LARGE_PACKAGE_PRICE * 100) * large_count,
    )
    return dict(
        monthly_price=price / 100,
        price_cents=price,
        small_unit=price / 100 / small_count,
        large_unit=price / 100 / large_count,
        small_saving=max(
            0, (1 - (price / 100 / small_count) / SMALL_PACKAGE_PRICE) * 100
        ),
        large_saving=max(
            0, (1 - (price / 100 / large_count) / LARGE_PACKAGE_PRICE) * 100
        ),
        small_price=SMALL_PACKAGE_PRICE,
        large_price=LARGE_PACKAGE_PRICE,
        small_count=small_count,
        large_count=large_count,
        large_equivalent=LARGE_CREDIT_COST // SMALL_CREDIT_COST,
        saving_percent=max(0, (avulso_cents - price) * 100 // avulso_cents),
        small_sum=SMALL_PACKAGE_MAX_SUM,
        large_sum=LARGE_PACKAGE_MAX_SUM,
        small_weight=SMALL_PACKAGE_MAX_WEIGHT,
        large_weight=LARGE_PACKAGE_MAX_WEIGHT,
    )


def offered_rules(db, cents=None):
    cents = pricing.current(db) if cents is None else cents
    return PLAN_RULES.replace("R$ 33,90", "R$ " + pricing.money(cents))


def offered_version(db, cents=None):
    cents = pricing.current(db) if cents is None else cents
    return (
        PLAN_RULES_VERSION
        if cents == AMOUNT_CENTS
        else PLAN_RULES_VERSION + "-price-" + str(cents)
    )


def iso(value):
    return value.astimezone(TZ).isoformat()


def parse(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def month_after(start, anchor=None):
    year, month = (
        (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    )
    return start.replace(
        year=year,
        month=month,
        day=min(anchor or start.day, calendar.monthrange(year, month)[1]),
    )


def schema(db, id_type):
    db.execute(
        "CREATE TABLE IF NOT EXISTS plan_api_budget (minute TEXT NOT NULL,endpoint TEXT NOT NULL,calls INTEGER NOT NULL,PRIMARY KEY(minute,endpoint))"
    )
    db.execute("CREATE TABLE IF NOT EXISTS plan_gateway_lock (id INTEGER PRIMARY KEY)")
    db.execute("INSERT INTO plan_gateway_lock(id) VALUES(1) ON CONFLICT DO NOTHING")
    db.execute(
        """CREATE TABLE IF NOT EXISTS plan_accounts(client_id BIGINT PRIMARY KEY REFERENCES clients(id),created_at TEXT NOT NULL)"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS plan_orders (
        id TEXT PRIMARY KEY,client_id BIGINT NOT NULL REFERENCES clients(id),method TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,cycle_key TEXT NOT NULL,renewal_of TEXT,
        state TEXT NOT NULL,transaction_id TEXT UNIQUE,created_at TEXT NOT NULL,checked_at TEXT,
        check_after TEXT,paid_at TEXT,refunded_at TEXT,error_code TEXT,
        payer_document TEXT NOT NULL,callback_url TEXT NOT NULL,
        qr_text TEXT,expires_at TEXT,settlement_state TEXT,card_brand TEXT,card_last4 TEXT,
        rules_version TEXT NOT NULL,rules_text TEXT NOT NULL,accepted_at TEXT NOT NULL)"""
    )
    db.execute("""CREATE TABLE IF NOT EXISTS plan_admin_decisions (
        id TEXT PRIMARY KEY,order_id TEXT NOT NULL REFERENCES plan_orders(id),
        client_id BIGINT NOT NULL REFERENCES clients(id),action TEXT NOT NULL,
        actor_id BIGINT REFERENCES staff_users(id),actor_key TEXT NOT NULL,actor_name TEXT NOT NULL,
        reason TEXT NOT NULL,created_at TEXT NOT NULL,previous_state TEXT NOT NULL,final_state TEXT NOT NULL,
        UNIQUE(order_id,action))""")
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_one_entitlement_order_v2 ON plan_orders(client_id,cycle_key) WHERE state IN ('creating','uncertain','pending','approved','under_review','paid','manual_approved','cancel_requested')"
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_one_open_order ON plan_orders(client_id,cycle_key) WHERE state IN ('creating','uncertain','pending','approved','under_review','paid')"
    )
    db.execute("""CREATE TABLE IF NOT EXISTS plan_cycles (
        id TEXT PRIMARY KEY,order_id TEXT NOT NULL UNIQUE REFERENCES plan_orders(id),
        client_id BIGINT NOT NULL REFERENCES clients(id),starts_at TEXT NOT NULL,ends_at TEXT NOT NULL,
        anchor_day INTEGER NOT NULL,granted_at TEXT,used_credits INTEGER NOT NULL DEFAULT 0,
        suspended INTEGER NOT NULL DEFAULT 0,review_required INTEGER NOT NULL DEFAULT 0,
        CHECK(used_credits>=0 AND used_credits<=10))""")
    db.execute(f"""CREATE TABLE IF NOT EXISTS plan_credit_ledger (
        id {id_type},cycle_id TEXT NOT NULL REFERENCES plan_cycles(id),kind TEXT NOT NULL,
        amount INTEGER NOT NULL,package_id BIGINT UNIQUE REFERENCES packages(id),package_ref TEXT,
        recorded_at TEXT NOT NULL,original_base_cents INTEGER,
        UNIQUE(cycle_id,kind,package_id))""")
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_one_grant ON plan_credit_ledger(cycle_id) WHERE kind='grant'"
    )
    db.execute(f"""CREATE TABLE IF NOT EXISTS plan_payment_events (
        id {id_type},order_id TEXT REFERENCES plan_orders(id),transaction_id TEXT,
        event_key TEXT NOT NULL UNIQUE,event_type TEXT NOT NULL,received_at TEXT NOT NULL,
        processed_at TEXT,outcome TEXT NOT NULL,reason TEXT)""")
    db.execute("""CREATE TABLE IF NOT EXISTS plan_reminders (
        cycle_id TEXT NOT NULL REFERENCES plan_cycles(id),day TEXT NOT NULL,state TEXT NOT NULL,
        claimed_at TEXT NOT NULL,sent_at TEXT,PRIMARY KEY(cycle_id,day))""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_plan_pending ON plan_orders(state,check_after)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_plan_cycles_owner ON plan_cycles(client_id,starts_at,ends_at)"
    )


def claim_api_slot(db, method, path, value):
    """Shared cross-worker budget below the documented provider rate limits."""
    endpoint = (
        method + " " + ("/transactions" if path.startswith("/transactions") else path)
    )
    minute = iso(value.replace(second=0, microsecond=0))
    maximum = 4 if method == "POST" else 15
    db.begin_write()
    row = db.execute(
        """INSERT INTO plan_api_budget(minute,endpoint,calls) VALUES(?,?,1)
        ON CONFLICT(minute,endpoint) DO UPDATE SET calls=plan_api_budget.calls+1
        WHERE plan_api_budget.calls<? RETURNING calls""",
        (minute, endpoint, maximum),
    ).fetchone()
    db.execute(
        "DELETE FROM plan_api_budget WHERE minute<?", (iso(value - timedelta(hours=2)),)
    )
    db.commit()
    if not row:
        raise GatewayError(
            "Limite de consultas atingido. Aguarde antes de consultar novamente.",
            code="rate_limited",
        )


def lock_account(db, client_id, value):
    db.execute(
        "INSERT INTO plan_accounts(client_id,created_at) VALUES(?,?) ON CONFLICT DO NOTHING",
        (client_id, iso(value)),
    )
    db.execute(
        "SELECT client_id FROM plan_accounts WHERE client_id=?"
        + (" FOR UPDATE" if db.postgres else ""),
        (client_id,),
    ).fetchone()


def cycles(db, client_id, value):
    rows = [
        dict(r)
        for r in db.execute(
            "SELECT * FROM plan_cycles WHERE client_id=? ORDER BY starts_at DESC",
            (client_id,),
        ).fetchall()
    ]
    active = [r for r in rows if parse(r["starts_at"]) <= value < parse(r["ends_at"])]
    upcoming = [r for r in reversed(rows) if parse(r["starts_at"]) > value]
    current = next(
        (r for r in active if not r["suspended"]), active[0] if active else None
    )
    future = next(
        (r for r in upcoming if not r["suspended"]), upcoming[0] if upcoming else None
    )
    return current, future, rows


def grant_due(db, client_id, value):
    # Caller holds the account lock and write transaction.
    for row in db.execute(
        "SELECT * FROM plan_cycles WHERE client_id=? AND granted_at IS NULL AND suspended=0",
        (client_id,),
    ).fetchall():
        if parse(row["starts_at"]) <= value < parse(row["ends_at"]):
            db.execute(
                "UPDATE plan_cycles SET granted_at=? WHERE id=? AND granted_at IS NULL",
                (iso(value), row["id"]),
            )
            db.execute(
                "INSERT INTO plan_credit_ledger(cycle_id,kind,amount,recorded_at) VALUES(?,'grant',10,?) ON CONFLICT DO NOTHING",
                (row["id"], iso(value)),
            )


def next_cycle_key(db, client_id, value):
    current, _, _ = cycles(db, client_id, value)
    if current:
        return "after:" + current["id"]
    previous = db.execute(
        "SELECT id FROM plan_cycles WHERE client_id=? ORDER BY ends_at DESC LIMIT 1",
        (client_id,),
    ).fetchone()
    return "restart:" + previous["id"] if previous else "activation"


def reserve_order(
    db,
    owner,
    method,
    value,
    settings,
    *,
    expected_key=None,
    accepted_by_client=True,
    expected_amount=None,
    retry_from=None,
):
    db.commit()
    db.begin_write()
    lock_account(db, owner["id"], value)
    unresolved = db.execute(
        "SELECT * FROM plan_orders WHERE client_id=? AND (state IN ('creating','uncertain','pending','approved','under_review','cancel_requested') OR error_code IN ('archived_payment_review','payment_after_cancellation')) ORDER BY created_at DESC LIMIT 1",
        (owner["id"],),
    ).fetchone()
    if unresolved:
        db.commit()
        return dict(unresolved), None
    if method != "administrative" and not settings.new_charges_enabled:
        db.commit()
        return None, "Novas cobranças Fast estão pausadas para revisão da integração."
    original = None
    if retry_from:
        original = db.execute(
            "SELECT * FROM plan_orders WHERE id=? AND client_id=?",
            (retry_from, owner["id"]),
        ).fetchone()
        child = db.execute(
            "SELECT * FROM plan_orders WHERE retry_of=? ORDER BY created_at DESC LIMIT 1",
            (retry_from,),
        ).fetchone()
        if child:
            db.commit()
            return dict(child), None
        if (
            not original
            or original["state"] not in ("expired", "cancelled")
            or original["paid_at"]
            or original["archived_at"]
            or original["error_code"]
            or original["local_method"] != "Pix Fast"
            or (original["cancel_state"] and original["state"] != "expired")
        ):
            db.commit()
            return (
                None,
                "A cobrança anterior precisa ter o encerramento sem pagamento confirmado pela Fast.",
            )
    current, future, _ = cycles(db, owner["id"], value)
    if future and not future["suspended"]:
        db.commit()
        return (
            None,
            "O próximo ciclo já está confirmado. Nenhuma nova cobrança foi criada.",
        )
    if current and current["suspended"]:
        db.commit()
        return (
            None,
            "Há um pagamento em revisão administrativa. Seus pacotes recebidos continuam disponíveis para retirada.",
        )
    key = next_cycle_key(db, owner["id"], value)
    if expected_key is not None and key != expected_key:
        db.commit()
        return None, "O ciclo mudou. Abra a confirmação novamente antes de cobrar."
    old = db.execute(
        "SELECT * FROM plan_orders WHERE client_id=? AND cycle_key=? AND state IN ('creating','uncertain','pending','approved','under_review','paid','manual_approved','cancel_requested') ORDER BY created_at DESC LIMIT 1",
        (owner["id"], key),
    ).fetchone()
    if old:
        db.commit()
        return dict(old), None
    if original and original["cycle_key"] != key:
        db.commit()
        return (
            None,
            "O ciclo mudou. Confira seu plano antes de iniciar outro pagamento.",
        )
    price = original["amount_cents"] if original else pricing.current(db)
    if expected_amount is not None and str(price) != str(expected_amount):
        db.commit()
        return None, "A mensalidade mudou. Confira o novo valor antes de confirmar."
    db.execute(
        "SELECT id FROM plan_gateway_lock WHERE id=1"
        + (" FOR UPDATE" if db.postgres else "")
    ).fetchone()
    # Four creation attempts/minute is the provider's documented global cap.
    recent = db.execute(
        "SELECT COUNT(*) n FROM plan_orders WHERE created_at>? AND method!='administrative'",
        (iso(value - timedelta(minutes=1)),),
    ).fetchone()["n"]
    if recent >= 4:
        db.commit()
        return None, "Aguarde um minuto antes de criar outra cobrança."
    oid = str(uuid.uuid4())
    stamp = iso(value)
    db.execute(
        """INSERT INTO plan_orders(id,client_id,method,amount_cents,cycle_key,renewal_of,state,created_at,payer_document,callback_url,rules_version,rules_text,accepted_at,local_method,creation_started_at,retry_of,root_order_id,policy_snapshot)
        VALUES(?,?,?,?,?,?,'creating',?,?,?,?,?,?,'Pix Fast','',?,?,?)""",
        (
            oid,
            owner["id"],
            method,
            price,
            key,
            current["id"] if current else None,
            stamp,
            re.sub(r"\D", "", owner["cpf"]),
            settings.callback(oid),
            original["rules_version"] if original else offered_version(db, price),
            original["rules_text"] if original else offered_rules(db, price),
            (
                original["accepted_at"]
                if original
                else (stamp if accepted_by_client else "")
            ),
            retry_from,
            (original["root_order_id"] or original["id"]) if original else None,
            original["policy_snapshot"] if original else None,
        ),
    )
    db.commit()
    return (
        dict(db.execute("SELECT * FROM plan_orders WHERE id=?", (oid,)).fetchone()),
        "create",
    )


def validate_transaction(order, data):
    if order["method"] == "administrative":
        raise GatewayError(code="wrong_payment_method")
    if not isinstance(data, dict):
        raise GatewayError(code="invalid_response")
    tid = transaction_id(data.get("id", data.get("transaction_id")))
    if order.get("transaction_id") and tid != order["transaction_id"]:
        raise GatewayError(
            "A transação não corresponde ao pedido.", code="identity_mismatch"
        )
    if data.get("payment_provider") != "fastflow" or not money_matches(
        data.get("amount"), order["amount_cents"]
    ):
        raise GatewayError(
            "Provedor ou valor divergente. O plano não foi liberado.",
            code="amount_provider_mismatch",
        )
    if data.get("notification_url") != order["callback_url"]:
        raise GatewayError(
            "Vínculo da transação com o pedido não confirmado.",
            code="identity_mismatch",
        )
    user = data.get("user") or {}
    if not isinstance(user, dict):
        raise GatewayError(code="invalid_response")
    document = str(user.get("cpf_cnpj", data.get("payer_document", "")))
    if (
        document
        and "".join(c for c in document if c.isdigit()) != order["payer_document"]
    ):
        raise GatewayError(
            "A transação pertence a outro cadastro.", code="identity_mismatch"
        )
    method = data.get("payment_method", "pix")
    if method != order["method"]:
        raise GatewayError("Forma de pagamento divergente.", code="identity_mismatch")
    return tid


def attach_creation_id(db, order_id, data, value):
    """Keep the API POST identifier, without granting credits from its summary."""
    if not isinstance(data, dict):
        raise GatewayError(code="invalid_response")
    tid = transaction_id(data.get("id"))
    if db.execute(
        "SELECT 1 FROM pickup_payment_orders WHERE transaction_id=?", (tid,)
    ).fetchone():
        raise GatewayError(code="identity_mismatch")
    original = db.execute(
        "SELECT * FROM plan_orders WHERE id=?", (order_id,)
    ).fetchone()
    if not original:
        raise GatewayError(code="unknown_order")
    if data.get("payment_provider") != "fastflow" or not money_matches(
        data.get("amount"), original["amount_cents"]
    ):
        raise GatewayError(code="amount_provider_mismatch")
    db.commit()
    db.begin_write()
    order = db.execute("SELECT * FROM plan_orders WHERE id=?", (order_id,)).fetchone()
    lock_account(db, order["client_id"], value)
    order = db.execute("SELECT * FROM plan_orders WHERE id=?", (order_id,)).fetchone()
    if (order["transaction_id"] and order["transaction_id"] != tid) or data.get(
        "payment_method", "pix"
    ) != order["method"]:
        db.rollback()
        raise GatewayError(code="identity_mismatch")
    if db.execute(
        "SELECT 1 FROM plan_orders WHERE transaction_id=? AND id!=?", (tid, order_id)
    ).fetchone():
        db.rollback()
        raise GatewayError(code="identity_mismatch")
    db.execute("UPDATE plan_orders SET transaction_id=? WHERE id=?", (tid, order_id))
    db.commit()
    return tid


def safe_time(value):
    try:
        return iso(parse(value)) if value else None
    except (TypeError, ValueError, AttributeError):
        return None


def fast_pix_window_ended(order, value):
    expires = safe_time(order["expires_at"])
    return (
        (order["local_method"] == "Pix Fast" or dict(order).get("method") == "pix")
        and order["state"] == "pending"
        and expires is not None
        and parse(expires) <= value
    )


def verified_pix_expired(order, data, value):
    """Call only after correlating a fresh provider lookup to this order."""
    if (
        order["local_method"] != "Pix Fast"
        or dict(order).get("method", "pix") != "pix"
        or order["state"] != "pending"
        or order["paid_at"]
        or data.get("status") != "pending"
    ):
        return False
    expires = safe_time(data.get("qr_code_expires_at"))
    same_qr = not data.get("qr_code_text") or data["qr_code_text"] == order["qr_text"]
    if not expires and not data.get("qr_code_expires_at") and same_qr:
        expires = safe_time(order["expires_at"])
    return bool(expires and parse(expires) <= value)


def payment_code(order, value):
    if (order["archived_at"] or not order["qr_text"] or dict(order).get("superseded_at")
        or order["error_code"] in ("superseded_payment_review", "prior_payment_review")):
        return None
    if order["local_method"] == "Pix AI":
        return (
            order["qr_text"]
            if order["state"] in ("pending", "approved", "under_review")
            else None
        )
    if order["local_method"] != "Pix Fast" and dict(order).get("method") != "pix":
        return None
    if order["state"] != "pending" or fast_pix_window_ended(order, value):
        return None
    return order["qr_text"]


def activate_order(db, order, value):
    order_id = order["id"]
    duplicate = db.execute(
        "SELECT id FROM plan_orders WHERE client_id=? AND cycle_key=? AND state IN ('paid','manual_approved') AND id!=?",
        (order["client_id"], order["cycle_key"], order_id),
    ).fetchone()
    if duplicate:
        db.rollback()
        raise GatewayError(
            "Mais de um pagamento para a mesma contratação; consulte o atendimento.",
            code="duplicate_remote",
        )
    current, future, _ = cycles(db, order["client_id"], value)
    if (future and not future["suspended"]) or (current and current["suspended"]):
        db.rollback()
        raise GatewayError(
            "Há mais de um pagamento para o próximo ciclo. Tratamento administrativo necessário.",
            code="duplicate_remote",
        )
    # Confirmed late payment never backdates a cycle over received parcels.
    start = parse(current["ends_at"]) if current and not current["suspended"] else value
    anchor = current["anchor_day"] if current else value.day
    end = month_after(start, anchor)
    cid = str(uuid.uuid4())
    db.execute(
        "INSERT INTO plan_cycles(id,order_id,client_id,starts_at,ends_at,anchor_day) VALUES(?,?,?,?,?,?)",
        (cid, order_id, order["client_id"], iso(start), iso(end), anchor),
    )
    grant_due(db, order["client_id"], value)


def apply_transaction(db, order_id, data, value, *, expire_pending_pix=False):
    original = db.execute(
        "SELECT * FROM plan_orders WHERE id=?", (order_id,)
    ).fetchone()
    if not original:
        raise GatewayError(code="unknown_order")
    tid = validate_transaction(dict(original), data)
    if db.execute(
        "SELECT 1 FROM pickup_payment_orders WHERE transaction_id=?", (tid,)
    ).fetchone():
        raise GatewayError(code="identity_mismatch")
    state = data.get("status")
    if state not in (
        "pending",
        "approved",
        "under_review",
        "paid",
        "expired",
        "cancelled",
        "refunded",
    ):
        raise GatewayError(
            "Situação do pagamento em análise. Consulte a administração.",
            code="unknown_status",
        )
    db.commit()
    db.begin_write()
    lock_account(db, original["client_id"], value)
    order = dict(
        db.execute(
            "SELECT * FROM plan_orders WHERE id=?"
            + (" FOR UPDATE" if db.postgres else ""),
            (order_id,),
        ).fetchone()
    )
    validate_transaction(order, data)
    locally_expired = expire_pending_pix and verified_pix_expired(order, data, value)
    if locally_expired:
        state = "expired"
    other = db.execute(
        "SELECT id FROM plan_orders WHERE transaction_id=? AND id!=?", (tid, order_id)
    ).fetchone()
    if other:
        db.rollback()
        raise GatewayError(
            "Transação já vinculada a outro pedido.", code="identity_mismatch"
        )
    qr = data.get("qr_code_text")
    expires = safe_time(data.get("qr_code_expires_at"))
    card = data.get("card") or {}
    if not isinstance(card, dict):
        raise GatewayError(code="invalid_response")
    brand = card.get("brand")
    last4 = card.get("last4")
    if not isinstance(brand, str) or not re_safe_brand(brand):
        brand = None
    if not isinstance(last4, str) or len(last4) != 4 or not last4.isdigit():
        last4 = None
    settlement = (
        data.get("receivable_status")
        if data.get("receivable_status")
        in ("scheduled", "settled", "paid", "cancelled", "refunded")
        else None
    )
    db.execute(
        """UPDATE plan_orders SET transaction_id=?,checked_at=?,check_after=?,qr_text=COALESCE(?,qr_text),expires_at=CASE WHEN ?=1 THEN ? ELSE expires_at END,settlement_state=?,card_brand=?,card_last4=?,error_code=CASE WHEN state IN ('admin_cancelled','cancel_requested') OR error_code IN ('duplicate_manual_and_fast_payment','archived_payment_review','payment_after_cancellation') THEN error_code ELSE NULL END WHERE id=?""",
        (
            tid,
            iso(value),
            iso(value + timedelta(seconds=20)),
            qr if isinstance(qr, str) and len(qr) <= 4096 else None,
            int(bool(data.get("qr_code_expires_at")) or (
                isinstance(qr, str) and bool(qr) and len(qr) <= 4096 and qr != order["qr_text"]
            )),
            expires,
            settlement,
            brand,
            last4,
            order_id,
        ),
    )
    cycle = db.execute(
        "SELECT * FROM plan_cycles WHERE order_id=?", (order_id,)
    ).fetchone()
    if locally_expired and not cycle:
        db.execute("UPDATE plan_orders SET cancel_state='closed' WHERE id=?", (order_id,))
        order["cancel_state"] = "closed"
    manual_approval = db.execute(
        "SELECT 1 FROM plan_admin_decisions WHERE order_id=? AND action='approve'",
        (order_id,),
    ).fetchone()
    if order["archived_at"] and state == "paid" and not cycle:
        db.execute(
            "UPDATE plan_orders SET state='paid',paid_at=COALESCE(paid_at,?),error_code='archived_payment_review' WHERE id=?",
            (iso(value), order_id),
        )
        db.commit()
        return
    # Restoring visibility must never turn an archived late payment into credits.
    if order["error_code"] == "archived_payment_review" and not cycle:
        if state == "paid":
            db.execute("UPDATE plan_orders SET state='paid',paid_at=COALESCE(paid_at,?) WHERE id=?", (iso(value), order_id))
        elif state == "refunded":
            db.execute("UPDATE plan_orders SET state='refunded',refunded_at=COALESCE(refunded_at,?) WHERE id=?", (iso(value), order_id))
        db.commit()
        return
    if order["cancel_state"] in ("external_pending", "closed") and not cycle:
        if state == "paid":
            db.execute(
                """UPDATE plan_orders SET state=CASE WHEN EXISTS (
                    SELECT 1 FROM plan_orders other
                    WHERE other.client_id=plan_orders.client_id
                    AND other.cycle_key=plan_orders.cycle_key AND other.id!=plan_orders.id
                    AND other.state IN ('creating','uncertain','pending','approved','under_review','paid','manual_approved','cancel_requested')
                ) THEN state ELSE 'paid' END,
                paid_at=COALESCE(paid_at,?),error_code='payment_after_cancellation' WHERE id=?""",
                (iso(value), order_id),
            )
        elif state == "refunded":
            db.execute(
                "UPDATE plan_orders SET state='refunded',refunded_at=COALESCE(refunded_at,?) WHERE id=?",
                (iso(value), order_id),
            )
        elif not order["paid_at"] and (
            order["cancel_state"] != "closed" or state in ("cancelled", "expired")
        ):
            db.execute(
                "UPDATE plan_orders SET state=?,cancel_state=? WHERE id=?",
                (
                    "admin_cancelled" if state == "cancelled" else state,
                    (
                        "closed"
                        if state in ("cancelled", "expired")
                        else "external_pending"
                    ),
                    order_id,
                ),
            )
        db.commit()
        return
    if order["state"] in ("admin_cancelled", "cancel_requested"):
        if state == "paid":
            db.execute(
                "UPDATE plan_orders SET paid_at=COALESCE(paid_at,?),error_code='payment_after_cancellation' WHERE id=?",
                (iso(value), order_id),
            )
        elif state == "refunded":
            db.execute(
                "UPDATE plan_orders SET refunded_at=COALESCE(refunded_at,?) WHERE id=?",
                (iso(value), order_id),
            )
        elif state in ("cancelled", "expired"):
            db.execute(
                "UPDATE plan_orders SET state='admin_cancelled',cancel_state='closed',error_code=CASE WHEN paid_at IS NULL THEN NULL ELSE error_code END WHERE id=?",
                (order_id,),
            )
        db.commit()
        return
    if manual_approval and order["manual_paid_at"]:
        if state == "paid":
            db.execute(
                "UPDATE plan_orders SET paid_at=COALESCE(paid_at,?),error_code='duplicate_manual_and_fast_payment' WHERE id=?",
                (iso(value), order_id),
            )
        elif state == "refunded":
            db.execute(
                "UPDATE plan_orders SET refunded_at=COALESCE(refunded_at,?) WHERE id=?",
                (iso(value), order_id),
            )
        db.commit()
        return
    if state == "refunded":
        db.execute(
            "UPDATE plan_orders SET state='refunded',refunded_at=COALESCE(refunded_at,?) WHERE id=?",
            (iso(value), order_id),
        )
        if cycle:
            db.execute(
                "UPDATE plan_cycles SET suspended=1,review_required=? WHERE id=?",
                (int(cycle["used_credits"] > 0), cycle["id"]),
            )
    elif order["refunded_at"]:
        # A stale paid event cannot resurrect refunded credits.
        if cycle:
            db.execute(
                "UPDATE plan_cycles SET review_required=1 WHERE id=?", (cycle["id"],)
            )
    elif cycle:
        if state == "paid":
            db.execute(
                "UPDATE plan_orders SET state='paid',paid_at=COALESCE(paid_at,?) WHERE id=?",
                (iso(value), order_id),
            )
        elif state not in ("pending", "approved") and (
            not manual_approval or order["paid_at"]
        ):
            db.execute(
                "UPDATE plan_cycles SET suspended=1,review_required=1 WHERE id=?",
                (cycle["id"],),
            )
    elif state == "paid":
        activate_order(db, order, value)
        db.execute(
            "UPDATE plan_orders SET state='paid',paid_at=? WHERE id=?",
            (iso(value), order_id),
        )
    else:
        db.execute("UPDATE plan_orders SET state=? WHERE id=?", (state, order_id))
    db.commit()


def re_safe_brand(value):
    import re

    return bool(re.fullmatch("[A-Za-z0-9 -]{1,32}", value))


def consume(db, client_id, package_id, package_ref, category, original_price, value):
    """Called after package INSERT within its transaction, before commit."""
    lock_account(db, client_id, value)
    grant_due(db, client_id, value)
    current, _, _ = cycles(db, client_id, value)
    cost = SMALL_CREDIT_COST if category == "Pequeno" else LARGE_CREDIT_COST
    if (
        not current
        or current["suspended"]
        or not current["granted_at"]
        or 10 - current["used_credits"] < cost
    ):
        return False
    result = db.execute(
        "UPDATE plan_cycles SET used_credits=used_credits+? WHERE id=? AND suspended=0 AND used_credits+?<=10",
        (cost, current["id"], cost),
    )
    if result.rowcount != 1:
        return False
    db.execute(
        "INSERT INTO plan_credit_ledger(cycle_id,kind,amount,package_id,package_ref,recorded_at,original_base_cents) VALUES(?,'consume',?,?,?,?,?)",
        (
            current["id"],
            -cost,
            package_id,
            package_ref,
            iso(value),
            int(round(original_price * 100)),
        ),
    )
    db.execute("UPDATE packages SET base_price=0 WHERE id=?", (package_id,))
    return True


def coverage(db, package_id):
    return db.execute(
        "SELECT * FROM plan_credit_ledger WHERE package_id=? AND kind='consume'",
        (package_id,),
    ).fetchone()


def coverage_description(db, package):
    entry = coverage(db, package["id"])
    if entry:
        cost = -entry["amount"]
        return f"Incluído no plano · {cost} crédito{'s' if cost != 1 else ''} já utilizado{'s' if cost != 1 else ''} · Recebimento: R$ 0,00."
    base = package["base_price"] if "base_price" in package.keys() else package["base"]
    amount = f"{base:.2f}".replace(".", ",")
    return f"Avulso · Recebimento: R$ {amount}."
