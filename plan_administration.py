"""Explicit administrator decisions, separate from provider payment evidence."""

import re
import uuid
import plan_service as service
from client_compliance import compliance
import client_actions
from fastflow_gateway import AMOUNT_CENTS, GatewayError

APPROVABLE = (
    "creating",
    "uncertain",
    "pending",
    "approved",
    "under_review",
    "expired",
    "cancelled",
    "failed",
    "declined",
)


def validate_actor(db, actor):
    if actor["id"] is None:
        return
    row = db.execute(
        "SELECT role,active,can_settings,deleted_at FROM staff_users WHERE id=?"
        + (" FOR UPDATE" if db.postgres else ""),
        (actor["id"],),
    ).fetchone()
    if (
        not row
        or row["role"] != "administrador"
        or not row["active"]
        or not row["can_settings"]
        or row["deleted_at"]
    ):
        raise ValueError(
            "Somente um administrador ativo com permissão de configurações pode realizar esta ação."
        )


def request_values(reason, operation_id):
    reason = str(reason).strip()
    if not 10 <= len(reason) <= 600:
        raise ValueError(
            "Informe um motivo de 10 a 600 caracteres, sem senhas ou códigos."
        )
    try:
        if str(uuid.UUID(operation_id)) != operation_id:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError(
            "Identificação da ação inválida. Abra a confirmação novamente."
        ) from None
    return reason


def previous(db, operation_id, client_id, action, actor, order_id=None):
    row = db.execute(
        "SELECT * FROM plan_admin_decisions WHERE id=?", (operation_id,)
    ).fetchone()
    if not row:
        return None
    if (
        row["client_id"] != client_id
        or row["action"] != action
        or row["actor_key"] != actor["key"]
        or (order_id and row["order_id"] != order_id)
    ):
        raise ValueError(
            "Esta confirmação pertence a outra ação. Abra a solicitação novamente."
        )
    return row["order_id"]


def record(db, order, action, actor, reason, operation_id, value, final_state):
    db.execute(
        """INSERT INTO plan_admin_decisions(id,order_id,client_id,action,actor_id,actor_key,actor_name,reason,created_at,previous_state,final_state)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            operation_id,
            order["id"],
            order["client_id"],
            action,
            actor["id"],
            actor["key"],
            actor["name"],
            reason,
            service.iso(value),
            order["state"],
            final_state,
        ),
    )


def approve(
    db,
    client_id,
    order_id,
    actor,
    reason,
    operation_id,
    value,
    settings,
    expected_key=None,
):
    reason = request_values(reason, operation_id)
    db.commit()
    db.begin_write()
    service.lock_account(db, client_id, value)
    validate_actor(db, actor)
    old = previous(db, operation_id, client_id, "approve", actor, order_id)
    if old:
        db.commit()
        return old
    person = db.execute(
        "SELECT * FROM clients WHERE id=?" + (" FOR UPDATE" if db.postgres else ""),
        (client_id,),
    ).fetchone()
    if not person or not person["active"]:
        raise ValueError(
            "Cliente inativo ou inexistente. Regularize a conta antes de validar."
        )
    missing = [x["label"] for x in compliance(db, person)[1]] + client_actions.blockers(
        db, person, service.iso(value)
    ).get("plans", [])
    if missing:
        raise ValueError("Regularize antes: " + "; ".join(missing))
    if order_id:
        row = db.execute(
            "SELECT * FROM plan_orders WHERE id=? AND client_id=?",
            (order_id, client_id),
        ).fetchone()
        if not row:
            raise ValueError("Pedido não encontrado para este cliente.")
    else:
        current, future, _ = service.cycles(db, client_id, value)
        if future and not future["suspended"]:
            raise ValueError(
                "O próximo ciclo já está confirmado. Nenhum novo ciclo foi criado."
            )
        if current and current["suspended"]:
            raise ValueError(
                "Há um pagamento em revisão. Confira o histórico antes de validar outro ciclo."
            )
        key = service.next_cycle_key(db, client_id, value)
        if expected_key != key:
            raise ValueError(
                "O ciclo mudou desde a prévia. Abra a confirmação novamente para evitar uma renovação duplicada."
            )
        row = db.execute(
            "SELECT * FROM plan_orders WHERE client_id=? AND cycle_key=? AND state IN ('creating','uncertain','pending','approved','under_review','paid','manual_approved') ORDER BY created_at DESC LIMIT 1",
            (client_id, key),
        ).fetchone()
        if not row:
            oid = str(uuid.uuid4())
            # An administrative grant is not a customer click, contract signature
            # or confirmed payment. Keep accepted_at empty and the origin distinct.
            db.execute(
                """INSERT INTO plan_orders(id,client_id,method,amount_cents,cycle_key,renewal_of,state,created_at,payer_document,callback_url,rules_version,rules_text,accepted_at)
                VALUES(?,?,'administrative',?,?,?,'creating',?,?,?,?,?,'')""",
                (
                    oid,
                    client_id,
                    AMOUNT_CENTS,
                    key,
                    current["id"] if current else None,
                    service.iso(value),
                    re.sub(r"\D", "", person["cpf"]),
                    settings.callback(oid),
                    service.PLAN_RULES_VERSION,
                    service.PLAN_RULES,
                ),
            )
            row = db.execute("SELECT * FROM plan_orders WHERE id=?", (oid,)).fetchone()
    order = dict(row)
    existing = db.execute(
        "SELECT 1 FROM plan_admin_decisions WHERE order_id=? AND action='approve'",
        (order["id"],),
    ).fetchone()
    if existing:
        db.commit()
        return order["id"]
    if (
        order["state"] not in APPROVABLE
        or order["paid_at"]
        or order["refunded_at"]
        or db.execute(
            "SELECT 1 FROM plan_cycles WHERE order_id=?", (order["id"],)
        ).fetchone()
    ):
        raise ValueError(
            "O pedido já possui confirmação, cancelamento administrativo ou histórico de ciclo. Nada foi alterado."
        )
    service.activate_order(db, order, value)
    db.execute(
        "UPDATE plan_orders SET state='manual_approved',error_code=NULL WHERE id=?",
        (order["id"],),
    )
    record(db, order, "approve", actor, reason, operation_id, value, "manual_approved")
    db.commit()
    return order["id"]


def cancel(db, app, order_id, actor, reason, operation_id, value, settings):
    reason = request_values(reason, operation_id)
    validate_actor(db, actor)
    db.commit()
    row = db.execute("SELECT * FROM plan_orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        raise ValueError("Pedido não encontrado.")
    order = dict(row)
    old = previous(db, operation_id, order["client_id"], "cancel", actor, order_id)
    if old:
        return old
    if order["state"] == "admin_cancelled":
        return order_id

    def unpaid(candidate):
        if (
            candidate["paid_at"]
            or candidate["refunded_at"]
            or candidate["state"] in ("paid", "manual_approved", "refunded")
            or db.execute(
                "SELECT 1 FROM plan_cycles WHERE order_id=?", (order_id,)
            ).fetchone()
        ):
            raise ValueError(
                "Somente solicitações não pagas e sem participação validada podem ser canceladas. O histórico foi preservado."
            )

    unpaid(order)
    if order["transaction_id"] or order["state"] not in ("failed", "declined"):
        if not settings.ready:
            raise ValueError(
                "A FastFlow precisa estar disponível para confirmar que a cobrança não foi paga antes de cancelar."
            )
        import plan_routes

        api = plan_routes.gateway(app)
        data = (
            api.get(order["transaction_id"])
            if order["transaction_id"]
            else api.find(order)
        )
        if data is None:
            raise ValueError(
                "A transação ainda está em correlação. Consulte a FastFlow antes de cancelar para evitar cobranças duplicadas."
            )
        service.apply_transaction(db, order_id, data, value)
        order = dict(
            db.execute("SELECT * FROM plan_orders WHERE id=?", (order_id,)).fetchone()
        )
        unpaid(order)
        if data["status"] not in ("cancelled", "expired"):
            api.cancel(order["transaction_id"])
            fresh = api.get(order["transaction_id"])
            service.apply_transaction(db, order_id, fresh, value)
            order = dict(
                db.execute(
                    "SELECT * FROM plan_orders WHERE id=?", (order_id,)
                ).fetchone()
            )
            unpaid(order)
            if fresh["status"] not in ("cancelled", "expired"):
                raise ValueError(
                    "A FastFlow ainda não confirmou o cancelamento. Consulte este pedido novamente; nenhuma solicitação foi encerrada localmente."
                )
    db.commit()
    db.begin_write()
    service.lock_account(db, order["client_id"], value)
    validate_actor(db, actor)
    old = previous(db, operation_id, order["client_id"], "cancel", actor, order_id)
    if old:
        db.commit()
        return old
    order = dict(
        db.execute("SELECT * FROM plan_orders WHERE id=?", (order_id,)).fetchone()
    )
    if order["state"] == "admin_cancelled":
        db.commit()
        return order_id
    unpaid(order)
    if order["state"] not in ("cancelled", "expired", "failed", "declined"):
        raise ValueError(
            "O pedido mudou durante a confirmação. Consulte-o antes de cancelar."
        )
    db.execute(
        "UPDATE plan_orders SET state='admin_cancelled',qr_text=NULL WHERE id=?",
        (order_id,),
    )
    record(db, order, "cancel", actor, reason, operation_id, value, "admin_cancelled")
    db.commit()
    return order_id
