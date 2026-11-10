"""One-time removal of provider data. No backup/export of charges is performed.
Only required financial entitlements and issued receipt content are retained.
"""

import json, re, uuid
from datetime import datetime
from core import TZ


def purge(db):
    blob = "BYTEA" if db.postgres else "BLOB"
    db.execute(
        "CREATE TABLE IF NOT EXISTS system_migrations(name TEXT PRIMARY KEY,applied_at TEXT NOT NULL)"
    )
    db.execute(
        f"CREATE TABLE IF NOT EXISTS preserved_plan_documents(id TEXT PRIMARY KEY,client_id BIGINT NOT NULL REFERENCES clients(id),document {blob} NOT NULL,created_at TEXT NOT NULL)"
    )
    marker = "remove_legacy_provider_v1"
    if db.execute("SELECT 1 FROM system_migrations WHERE name=?", (marker,)).fetchone():
        return
    from plan_receipt import build

    stamp = datetime.now(TZ).isoformat()
    for table in ("plan_payment_events", "pickup_payment_events"):
        db.execute("DROP TABLE IF EXISTS " + table)
    plans = db.execute(
        "SELECT * FROM plan_orders WHERE local_method='Pix Fast' OR (method IN ('pix','card') AND COALESCE(local_method,'') NOT IN ('Pix AI','Dinheiro'))"
    ).fetchall()
    for raw in plans:
        old = dict(raw)
        oid = old["id"]
        cycle = db.execute(
            "SELECT * FROM plan_cycles WHERE order_id=?", (oid,)
        ).fetchone()
        if cycle or old.get("paid_at") or old.get("manual_paid_at"):
            person = db.execute(
                "SELECT * FROM clients WHERE id=?", (old["client_id"],)
            ).fetchone()
            pdf = build(old, person, cycle, "Pix Fast").getvalue()
            db.execute(
                "INSERT INTO preserved_plan_documents(id,client_id,document,created_at) VALUES(?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (oid, old["client_id"], pdf, old["created_at"]),
            )
        if cycle:
            # A credit entitlement is not a provider charge or a manual payment confirmation.
            nid = str(uuid.uuid4())
            db.execute(
                "INSERT INTO plan_orders(id,client_id,method,amount_cents,cycle_key,renewal_of,state,created_at,payer_document,callback_url,rules_version,rules_text,accepted_at,local_method,paid_at,refunded_at) VALUES(?,?,'administrative',?,?,?,'historical_entitlement',?,?,?,?,?,?,?,?,?)",
                (
                    nid,
                    old["client_id"],
                    old["amount_cents"],
                    old["cycle_key"],
                    old["renewal_of"],
                    old["created_at"],
                    old["payer_document"],
                    "local:" + nid,
                    old["rules_version"],
                    old["rules_text"],
                    old["accepted_at"],
                    "Participação anterior",
                    old["paid_at"],
                    old["refunded_at"],
                ),
            )
            db.execute("UPDATE plan_cycles SET order_id=? WHERE order_id=?", (nid, oid))
        db.execute("DELETE FROM plan_admin_decisions WHERE order_id=?", (oid,))
        db.execute("DELETE FROM plan_orders WHERE id=?", (oid,))
    charges = db.execute(
        "SELECT * FROM pickup_payment_orders WHERE local_method='Pix Fast' OR (COALESCE(local_method,'') NOT IN ('Pix AI','Dinheiro','Pagamento anterior') AND (transaction_id IS NOT NULL OR callback_url LIKE ?))",
        ("%/webhooks/fastflow%",),
    ).fetchall()
    for raw in charges:
        old = dict(raw)
        oid = old["id"]
        has_credit = db.execute(
            "SELECT 1 FROM package_prepayments WHERE order_id=?", (oid,)
        ).fetchone()
        if has_credit:
            nid = str(uuid.uuid4())
            db.execute(
                "INSERT INTO pickup_payment_orders(id,client_id,amount_cents,state,callback_url,created_at,snapshot,quote_key,created_by,local_method,paid_at,advance,error_code) VALUES(?,?,?,?,?,?,?,?,?,'Pagamento anterior',?,1,?)",
                (
                    nid,
                    old["client_id"],
                    old["amount_cents"],
                    old["state"],
                    "local:" + nid,
                    old["created_at"],
                    old["snapshot"],
                    old["quote_key"],
                    "historical_entitlement",
                    old["paid_at"],
                    ("financial_review_required" if old["error_code"] else None),
                ),
            )
            db.execute(
                "UPDATE package_prepayments SET order_id=? WHERE order_id=?", (nid, oid)
            )
            for p in db.execute(
                "SELECT package_id FROM pickup_payment_packages WHERE order_id=?",
                (oid,),
            ).fetchall():
                db.execute(
                    "INSERT INTO pickup_payment_packages(order_id,package_id) VALUES(?,?)",
                    (nid, p["package_id"]),
                )
        db.execute("DELETE FROM pickup_payment_reservations WHERE order_id=?", (oid,))
        db.execute("DELETE FROM pickup_payment_packages WHERE order_id=?", (oid,))
        db.execute("DELETE FROM pickup_payment_orders WHERE id=?", (oid,))
    # Existing local charges once used a provider-shaped callback despite never calling it.
    db.execute(
        "UPDATE plan_orders SET callback_url='local:' || id WHERE method='administrative'"
    )
    db.execute(
        "UPDATE pickup_payment_orders SET callback_url='local:' || id WHERE local_method IN ('Pix AI','Dinheiro','Pagamento anterior')"
    )
    # Remove the provider's event/deduplication/budget data only. Financial tables survive.
    for table in (
        "plan_payment_events",
        "pickup_payment_events",
        "plan_api_budget",
        "plan_gateway_lock",
    ):
        db.execute("DROP TABLE IF EXISTS " + table)
    db.execute(
        "DELETE FROM audit_logs WHERE LOWER(COALESCE(details,'')) LIKE ? OR LOWER(COALESCE(details,'')) LIKE ? OR action IN ('arquivar_cobranca','verificar_provedor_fast')",
        ("%fastflow%", "%pix fast%"),
    )
    for row in db.execute(
        "SELECT scope,rules FROM customer_payment_policies"
    ).fetchall():
        available = [
            r for r in json.loads(row["rules"]) if r["method"] in ("Pix AI", "Dinheiro")
        ]
        for i, r in enumerate(sorted(available, key=lambda r: r["priority"])):
            r["priority"] = i + 1
            for key in ("text", "title"):
                if re.search(r"fast", r.get(key, ""), re.I):
                    r[key] = (
                        r["method"]
                        if key == "title"
                        else "Pagamento sujeito à conferência da equipe."
                    )
        db.execute(
            "UPDATE customer_payment_policies SET rules=? WHERE scope=?",
            (json.dumps(available, ensure_ascii=False), row["scope"]),
        )
    db.execute(
        "INSERT INTO system_migrations(name,applied_at) VALUES(?,?)", (marker, stamp)
    )
