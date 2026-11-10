"""Gmail plan reminders; no payment provider integration."""

from datetime import timedelta
from html import escape
from types import SimpleNamespace
from payment_domain import LocalSettings
import plan_service as service


def reminders(db, config, value, send_email):
    origin = LocalSettings.from_config(config).origin
    if not LocalSettings.from_config(config).ready:
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
        import plan_pricing

        renewal_price = plan_pricing.money(plan_pricing.current(db))
        date = end.strftime("%d/%m/%Y às %H:%M")
        link = origin + "/cliente/plano"
        body = f"Olá, {row['name']}!\n\nSeu Plano ME GUARDE Mais vence em {date} (America/Sao_Paulo).\nRenovação: R$ {renewal_price} por ciclo mensal, com 10 créditos.\n\nRenovar meu plano: {link}\n\nSem débito automático. Excedentes e atraso são cobrados separadamente.\n\nPONTO DE COLETA ADELINHA\nME GUARDE — COMPROU? DEIXA COM A GENTE."
        html = f'<div style="font-family:Arial,sans-serif;max-width:600px;margin:auto;color:#292929;line-height:1.6"><h1>Plano ME GUARDE Mais</h1><p>Olá, {escape(row["name"])}!</p><p>Vencimento: <b>{date}</b> (America/Sao_Paulo).</p><p>Renovação: <b>R$ {renewal_price}</b> por ciclo mensal, com 10 créditos.</p><p><a style="display:inline-block;padding:14px 22px;background:#e34a00;color:white;border-radius:12px" href="{escape(link,quote=True)}">Renovar meu plano</a></p><p>Sem débito automático. Excedentes e atraso são cobrados separadamente.</p><p>PONTO DE COLETA ADELINHA<br>ME GUARDE — COMPROU? DEIXA COM A GENTE.</p></div>'
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
