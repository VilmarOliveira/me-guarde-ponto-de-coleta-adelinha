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
    payment_method=None,
    payment_confirmed=False,
):
    reason = request_values(reason, operation_id)
    if payment_method not in ("Pix AI", "Dinheiro") or not payment_confirmed:
        raise ValueError(
            "Escolha Pix AI ou Dinheiro e confirme o recebimento. Pix Fast exige confirmação pela API."
        )
    if payment_method == "Pix AI":
        setting = db.execute(
            "SELECT pix_key_type,pix_receiver_name FROM settings WHERE id=1"
        ).fetchone()
        if (
            not setting
            or setting["pix_key_type"] != "CNPJ"
            or not setting["pix_receiver_name"]
        ):
            raise ValueError("Configure a chave CNPJ e o titular para usar Pix AI.")
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
    if order["method"] == "pix" and order["state"] in service.OPEN_STATES + (
        "cancel_requested",
    ):
        raise ValueError(
            "Esta solicitação é Pix Fast. Aguarde a API ou cancele a cobrança antes de mudar a modalidade; não é permitido marcar Pix Fast manualmente como pago."
        )
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
        "UPDATE plan_orders SET state='manual_approved',local_method=?,manual_paid_at=?,error_code=NULL WHERE id=?",
        (payment_method, service.iso(value), order["id"]),
    )
    record(db, order, "approve", actor, reason, operation_id, value, "manual_approved")
    db.execute(
        "UPDATE plan_admin_decisions SET payment_method=?,amount_cents=?,result=? WHERE id=?",
        (payment_method, AMOUNT_CENTS, "manually_confirmed", operation_id),
    )
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
    if previous(db, operation_id, order["client_id"], "cancel", actor, order_id):
        return order_id
    if order["state"] == "admin_cancelled":
        return order_id

    def unpaid(candidate):
        if (
            candidate["paid_at"]
            or candidate["manual_paid_at"]
            or candidate["refunded_at"]
            or candidate["state"] in ("paid", "manual_approved", "refunded")
            or db.execute(
                "SELECT 1 FROM plan_cycles WHERE order_id=?", (order_id,)
            ).fetchone()
        ):
            raise ValueError(
                "Somente solicitações não pagas e sem participação validada podem ser canceladas. Pagamento confirmado exige análise apropriada."
            )

    unpaid(order)
    failure = None
    closed = False
    data = None
    no_external = not order["transaction_id"] and (
        order["state"] in ("failed", "declined")
        or (order["state"] == "creating" and order["creation_started_at"] == "")
    )
    if no_external:
        closed = True
    else:
        try:
            if not settings.ready:
                raise GatewayError(
                    "Cancelamento aguardando verificação", code="not_configured"
                )
            import plan_routes

            api = plan_routes.gateway(app)
            data = (
                api.get(order["transaction_id"])
                if order["transaction_id"]
                else api.find(order)
            )
            if data is None:
                raise GatewayError(
                    "Cancelamento aguardando verificação", code="awaiting_correlation"
                )
            service.apply_transaction(db, order_id, data, value)
            order = dict(
                db.execute(
                    "SELECT * FROM plan_orders WHERE id=?", (order_id,)
                ).fetchone()
            )
            unpaid(order)
            if data["status"] not in ("cancelled", "expired"):
                api.cancel(order["transaction_id"])
                data = api.get(order["transaction_id"])
                service.apply_transaction(db, order_id, data, value)
                order = dict(
                    db.execute(
                        "SELECT * FROM plan_orders WHERE id=?", (order_id,)
                    ).fetchone()
                )
                unpaid(order)
            closed = data["status"] in ("cancelled", "expired")
        except GatewayError as error:
            db.rollback()
            failure = error
    db.commit()
    db.begin_write()
    service.lock_account(db, order["client_id"], value)
    validate_actor(db, actor)
    if previous(db, operation_id, order["client_id"], "cancel", actor, order_id):
        db.commit()
        return order_id
    order = dict(
        db.execute(
            "SELECT * FROM plan_orders WHERE id=?"
            + (" FOR UPDATE" if db.postgres else ""),
            (order_id,),
        ).fetchone()
    )
    unpaid(order)
    if no_external:
        # Creation may have started between the first read and this lock.
        closed = not order["transaction_id"] and (
            order["state"] in ("failed", "declined")
            or (order["state"] == "creating" and order["creation_started_at"] == "")
        )
    state = "admin_cancelled" if closed else "cancel_requested"
    db.execute(
        "UPDATE plan_orders SET state=?,cancel_state=?,qr_text=NULL,error_code=?,check_after=NULL WHERE id=?",
        (
            state,
            "closed" if closed else "external_pending",
            None if closed else "cancel_awaiting_verification",
            order_id,
        ),
    )
    existing = db.execute(
        "SELECT id FROM plan_admin_decisions WHERE order_id=? AND action='cancel'",
        (order_id,),
    ).fetchone()
    if not existing:
        record(db, order, "cancel", actor, reason, operation_id, value, state)
        db.execute(
            "UPDATE plan_admin_decisions SET result=?,http_status=?,http_method=?,http_endpoint=? WHERE id=?",
            (
                "closed" if closed else "awaiting_verification",
                getattr(failure, "http_status", None),
                getattr(failure, "http_method", None),
                getattr(failure, "http_endpoint", None),
                operation_id,
            ),
        )
    db.commit()
    return order_id


def create_fast(
    db, app, person, order, actor, reason, operation_id, value, settings, expected_key
):
    reason = request_values(reason, operation_id)
    validate_actor(db, actor)
    db.commit()
    prev = previous(
        db, operation_id, person["id"], "charge", actor, order["id"] if order else None
    )
    if prev:
        return prev
    if not settings.ready:
        raise ValueError("Pix Fast não configurado. Escolha Pix AI ou Dinheiro.")
    missing = [x["label"] for x in compliance(db, person)[1]] + client_actions.blockers(
        db, person, service.iso(value)
    ).get("plans", [])
    if not person["active"] or missing:
        raise ValueError("Regularize o cadastro e o contrato antes da contratação.")
    if order:
        if order["method"] != "pix" or order["state"] not in service.OPEN_STATES:
            raise ValueError("Este pedido não permite nova cobrança FastFlow.")
        item = order
        mode = None
    else:
        item, mode = service.reserve_order(
            db,
            person,
            "pix",
            value,
            settings,
            expected_key=expected_key,
            accepted_by_client=False,
        )
        if not item:
            raise ValueError(mode)
    db.commit()
    db.begin_write()
    service.lock_account(db, person["id"], value)
    prior = db.execute(
        "SELECT 1 FROM plan_admin_decisions WHERE order_id=? AND action='charge'",
        (item["id"],),
    ).fetchone()
    if not prior:
        record(db, item, "charge", actor, reason, operation_id, value, item["state"])
        db.execute(
            "UPDATE plan_admin_decisions SET payment_method='Pix Fast',amount_cents=?,result='pending' WHERE id=?",
            (AMOUNT_CENTS, operation_id),
        )
    db.commit()
    import plan_charge

    problem = plan_charge.start(db, app, item, person, value)
    return item["id"]
