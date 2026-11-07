"""Persistent FastFlow reconciliation and Gmail reminders. Never creates charges."""

from datetime import timedelta
from html import escape
from types import SimpleNamespace
from fastflow_gateway import Settings, GatewayError
import plan_service as service
import plan_routes


def reconcile(db, config, value, limit=12):
    app = SimpleNamespace(config=config)
    ready = Settings.from_config(config).ready
    count = 0
    pending = (
        db.execute(
            "SELECT * FROM plan_payment_events WHERE outcome='pending' ORDER BY id LIMIT ?",
            (min(limit, 4),),
        ).fetchall()
        if ready
        else []
    )
    for event in pending:
        try:
            data = plan_routes.gateway(app).get(event["transaction_id"])
            service.apply_transaction(db, event["order_id"], data, value)
            if event["event_type"] == "transaction.paid" and data["status"] != "paid":
                continue
            db.execute(
                "UPDATE plan_payment_events SET outcome='processed',processed_at=?,reason=NULL WHERE id=?",
                (service.iso(value), event["id"]),
            )
            db.commit()
            count += 1
        except GatewayError as error:
            db.rollback()
            db.execute(
                "UPDATE plan_payment_events SET reason=? WHERE id=?",
                (error.code, event["id"]),
            )
            if error.code in (
                "identity_mismatch",
                "amount_provider_mismatch",
                "duplicate_remote",
                "unknown_status",
            ):
                db.execute(
                    "UPDATE plan_cycles SET suspended=1,review_required=1 WHERE order_id=?",
                    (event["order_id"],),
                )
            db.commit()
    # Paid orders are also rechecked periodically to recover missed refunds.
    rows = (
        db.execute(
            """SELECT id FROM plan_orders WHERE
        (method!='administrative' AND state IN ('creating','uncertain','pending','approved','under_review','cancel_requested') AND (check_after IS NULL OR check_after<=?))
        OR (state IN ('paid','manual_approved','admin_cancelled') AND method!='administrative' AND (checked_at IS NULL OR checked_at<?))
        ORDER BY COALESCE(checked_at,''),created_at LIMIT ?""",
            (service.iso(value), service.iso(value - timedelta(hours=6)), limit),
        ).fetchall()
        if ready
        else []
    )
    for row in rows:
        count += int(plan_routes.refresh(db, app, row["id"], value))
    if ready:
        import pickup_payments

        pickup_payments.reconcile(db, app, value)
    for account in db.execute(
        "SELECT DISTINCT client_id FROM plan_cycles WHERE granted_at IS NULL AND suspended=0 AND starts_at<=? AND ends_at>?",
        (service.iso(value), service.iso(value)),
    ).fetchall():
        db.commit()
        db.begin_write()
        service.lock_account(db, account["client_id"], value)
        service.grant_due(db, account["client_id"], value)
        db.commit()
    return count


def reminders(db, config, value, send_email):
    origin = Settings.from_config(config).origin
    if not Settings.from_config(config).ready:
        return 0
    today = value.date()
    sent_count = 0
    rows = db.execute(
        "SELECT p.*,c.name,c.email,c.active FROM plan_cycles p JOIN clients c ON c.id=p.client_id WHERE p.suspended=0"
    ).fetchall()
    for row in rows:
        end = service.parse(row["ends_at"])
        if (
            not row["active"]
            or not row["email"]
            or today not in (end.date() - timedelta(days=1), end.date())
        ):
            continue
        # Claim before calling Gmail. An uncertain delivery is never resent
        # automatically, as the Gmail interface has no idempotency key.
        db.commit()
        db.begin_write()
        service.lock_account(db, row["client_id"], value)
        renewed = db.execute(
            "SELECT 1 FROM plan_cycles p JOIN plan_orders o ON o.id=p.order_id WHERE p.client_id=? AND p.starts_at>=? AND p.id!=? AND p.suspended=0 AND o.state IN ('paid','manual_approved')",
            (row["client_id"], row["ends_at"], row["id"]),
        ).fetchone()
        latest = db.execute(
            "SELECT suspended FROM plan_cycles WHERE id=?", (row["id"],)
        ).fetchone()
        if renewed or latest["suspended"]:
            db.commit()
            continue
        claimed = db.execute(
            "INSERT INTO plan_reminders(cycle_id,day,state,claimed_at) VALUES(?,?,'claimed',?) ON CONFLICT DO NOTHING RETURNING cycle_id",
            (row["id"], today.isoformat(), service.iso(value)),
        ).fetchone()
        db.commit()
        if not claimed:
            continue
        # A paid renewal and delivery serialize on the same client lock.
        db.begin_write()
        service.lock_account(db, row["client_id"], value)
        renewed = db.execute(
            "SELECT 1 FROM plan_cycles p JOIN plan_orders o ON o.id=p.order_id WHERE p.client_id=? AND p.starts_at>=? AND p.id!=? AND p.suspended=0 AND o.state IN ('paid','manual_approved')",
            (row["client_id"], row["ends_at"], row["id"]),
        ).fetchone()
        if renewed:
            db.execute(
                "UPDATE plan_reminders SET state='suppressed' WHERE cycle_id=? AND day=?",
                (row["id"], today.isoformat()),
            )
            db.commit()
            continue
        row = db.execute(
            "SELECT p.*,c.name,c.email,c.active FROM plan_cycles p JOIN clients c ON c.id=p.client_id WHERE p.id=?",
            (row["id"],),
        ).fetchone()
        if row["suspended"] or not row["active"] or not row["email"]:
            db.execute(
                "UPDATE plan_reminders SET state='suppressed' WHERE cycle_id=? AND day=?",
                (row["id"], today.isoformat()),
            )
            db.commit()
            continue
        date = end.strftime("%d/%m/%Y às %H:%M")
        link = origin + "/cliente/plano"
        body = f"Olá, {row['name']}!\n\nSeu Plano ME GUARDE Mais vence em {date} (America/Sao_Paulo).\nRenovação: R$ 33,90 por ciclo mensal, com 10 créditos.\n\nRenovar meu plano: {link}\n\nSem débito automático. Excedentes e atraso são cobrados separadamente.\n\nPONTO DE COLETA ADELINHA\nME GUARDE — COMPROU? DEIXA COM A GENTE."
        html = f'<div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;color:#292929;line-height:1.6"><h1>Plano ME GUARDE Mais</h1><p>Olá, {escape(row["name"])}!</p><p>Vencimento: <b>{date}</b> (America/Sao_Paulo).</p><p>Renovação: <b>R$ 33,90</b> por ciclo mensal, com 10 créditos.</p><p><a style="display:inline-block;padding:14px 22px;background:#e34a00;color:white;border-radius:12px" href="{escape(link,quote=True)}">Renovar meu plano</a></p><p>Sem débito automático. Excedentes e atraso são cobrados separadamente.</p><p>PONTO DE COLETA ADELINHA<br>ME GUARDE — COMPROU? DEIXA COM A GENTE.</p></div>'
        try:
            ok, _ = send_email(
                row["email"], "Renove seu Plano ME GUARDE Mais", body, html_body=html
            )
        except Exception:
            ok = False
        db.execute(
            "UPDATE plan_reminders SET state=?,sent_at=? WHERE cycle_id=? AND day=?",
            (
                "sent" if ok else "delivery_uncertain",
                service.iso(value) if ok else None,
                row["id"],
                today.isoformat(),
            ),
        )
        db.commit()
        sent_count += int(ok)
    return sent_count
