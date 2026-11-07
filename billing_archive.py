"""Archiving is presentation only; it never cancels a provider transaction."""

import json


def archive(db, table, oid, actor, stamp):
    if table not in ("plan_orders", "pickup_payment_orders"):
        raise ValueError("Cobrança inválida.")
    db.commit()
    db.begin_write()
    row = db.execute(
        "SELECT * FROM "
        + table
        + " WHERE id=?"
        + (" FOR UPDATE" if db.postgres else ""),
        (oid,),
    ).fetchone()
    if not row:
        raise ValueError("Cobrança não encontrada.")
    method = row["local_method"] or (
        "Pix Fast" if table == "plan_orders" and row["method"] == "pix" else ""
    )
    if method != "Pix Fast":
        raise ValueError("Arquivamento disponível para cobranças Pix Fast.")
    if row["archived_at"]:
        db.commit()
        return
    db.execute(
        "UPDATE " + table + " SET archived_at=?,archived_by=? WHERE id=?",
        (stamp, actor, oid),
    )
    db.execute(
        "INSERT INTO audit_logs(actor,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
        (
            actor,
            "arquivar_cobranca",
            table,
            None,
            json.dumps({"order_id": oid, "state": row["state"]}, ensure_ascii=False),
            stamp,
        ),
    )
    db.commit()
