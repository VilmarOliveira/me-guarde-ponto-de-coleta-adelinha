"""One-shot creation shared by customer and admin Pix Fast plan flows."""

from datetime import datetime
from core import TZ
from database import DatabaseIntegrityError
from fastflow_gateway import GatewayError
import plan_service as service


def start(db, app, order, owner, value, card=None):
    claim = db.execute(
        "UPDATE plan_orders SET creation_started_at=?,local_method=? WHERE id=? AND state='creating' AND creation_started_at='' RETURNING id",
        (
            service.iso(value),
            "Pix Fast" if order["method"] == "pix" else "Cartão",
            order["id"],
        ),
    ).fetchone()
    db.commit()
    if not claim:
        return None
    created = False
    import plan_routes

    try:
        api = plan_routes.gateway(app)
        data = api.create(order, owner, card)
        created = True
        tid = service.attach_creation_id(db, order["id"], data, value)
        service.apply_transaction(db, order["id"], api.get(tid), value)
        return None
    except (GatewayError, DatabaseIntegrityError) as error:
        db.rollback()
        code = getattr(error, "code", "database_conflict")
        state = (
            "uncertain"
            if created or getattr(error, "uncertain", False)
            else "declined" if code == "declined" else "failed"
        )
        db.execute(
            "UPDATE plan_orders SET state=?,error_code=? WHERE id=? AND state='creating'",
            (state, code, order["id"]),
        )
        db.commit()
        return (
            "A cobrança está em consulta; não criaremos outra automaticamente."
            if state == "uncertain"
            else (
                str(error)
                if isinstance(error, GatewayError)
                else "Não foi possível concluir a cobrança. Consulte este pedido."
            )
        )
