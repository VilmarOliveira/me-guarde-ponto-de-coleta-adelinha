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
from payment_domain import AMOUNT_CENTS, PaymentError

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
    db.execute("""CREATE TABLE IF NOT EXISTS plan_reminders (
        cycle_id TEXT NOT NULL REFERENCES plan_cycles(id),day TEXT NOT NULL,state TEXT NOT NULL,
        claimed_at TEXT NOT NULL,sent_at TEXT,PRIMARY KEY(cycle_id,day))""")
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_plan_pending ON plan_orders(state,check_after)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_plan_cycles_owner ON plan_cycles(client_id,starts_at,ends_at)"
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
    if method != "administrative":
        db.rollback()
        raise ValueError("Modalidade indisponível. Use Pix AI ou Dinheiro.")
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
    price = pricing.current(db)
    if expected_amount is not None and str(price) != str(expected_amount):
        db.commit()
        return None, "A mensalidade mudou. Confira o novo valor antes de confirmar."
    oid = str(uuid.uuid4())
    stamp = iso(value)
    db.execute(
        """INSERT INTO plan_orders(id,client_id,method,amount_cents,cycle_key,renewal_of,state,created_at,payer_document,callback_url,rules_version,rules_text,accepted_at,local_method,creation_started_at,retry_of,root_order_id,policy_snapshot)
        VALUES(?,?,?,?,?,?,'creating',?,?,?,?,?,?,'','',?,?,?)""",
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
            offered_version(db, price),
            offered_rules(db, price),
            stamp if accepted_by_client else "",
            None,
            None,
            None,
        ),
    )
    db.commit()
    return (
        dict(db.execute("SELECT * FROM plan_orders WHERE id=?", (oid,)).fetchone()),
        "create",
    )


def safe_time(value):
    try:
        return iso(parse(value)) if value else None
    except (TypeError, ValueError, AttributeError):
        return None


def payment_code(order, value):
    return (
        order["qr_text"]
        if order["local_method"] == "Pix AI"
        and order["state"] in ("pending", "approved", "under_review")
        and not order["archived_at"]
        else None
    )


def activate_order(db, order, value):
    order_id = order["id"]
    duplicate = db.execute(
        "SELECT id FROM plan_orders WHERE client_id=? AND cycle_key=? AND state IN ('paid','manual_approved') AND id!=?",
        (order["client_id"], order["cycle_key"], order_id),
    ).fetchone()
    if duplicate:
        db.rollback()
        raise PaymentError(
            "Mais de um pagamento para a mesma contratação; consulte o atendimento.",
            code="duplicate_remote",
        )
    current, future, _ = cycles(db, order["client_id"], value)
    if (future and not future["suspended"]) or (current and current["suspended"]):
        db.rollback()
        raise PaymentError(
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
