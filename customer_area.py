"""Owner-scoped view data for the six customer-area tabs."""

from datetime import timedelta
from flask import request, abort
import plan_service as plans
from client_compliance import records
import plan_routes

TABS = (
    ("visao-geral", "Visão geral", "home"),
    ("pacotes", "Meus pacotes", "box"),
    ("plano", "Meu plano", "wallet"),
    ("historico", "Histórico", "calendar"),
    ("dados", "Meus dados", "person"),
    ("contratos", "Contratos e autorizações", "contract"),
)


def context(db, client, value, fees):
    selected = request.args.get("aba", "visao-geral")
    if selected not in dict((k, v) for k, v, _ in TABS):
        abort(404)
    exists = db.execute(
        "SELECT 1 FROM plan_cycles WHERE client_id=? LIMIT 1", (client["id"],)
    ).fetchone()
    if exists:
        db.commit()
        db.begin_write()
        plans.lock_account(db, client["id"], value)
        plans.grant_due(db, client["id"], value)
        db.commit()
    current, future, history = plans.cycles(db, client["id"], value)
    counts = db.execute(
        "SELECT COUNT(*) total,COALESCE(SUM(CASE WHEN status='aguardando_retirada' THEN 1 ELSE 0 END),0) waiting,COALESCE(SUM(CASE WHEN status='retirado' THEN 1 ELSE 0 END),0) picked FROM packages WHERE client_id=?",
        (client["id"],),
    ).fetchone()
    available = (
        10 - current["used_credits"]
        if current and current["granted_at"] and not current["suspended"]
        else 0
    )
    withdrawals = (
        db.execute(
            """SELECT pu.*,MIN(pp.package_id) first_package,COUNT(*) own_count,
        SUM(COALESCE(pp.prepaid_amount,0)) own_prepaid,SUM(pp.base_amount) own_base,SUM(pp.late_amount) own_late,SUM(CASE WHEN EXISTS(SELECT 1 FROM plan_credit_ledger l WHERE l.package_id=pp.package_id) THEN 1 ELSE 0 END) covered_count,
        (SELECT COUNT(*) FROM pickup_packages allp WHERE allp.pickup_id=pu.id) all_count
        FROM pickups pu JOIN pickup_packages pp ON pp.pickup_id=pu.id
        WHERE pp.client_id=? GROUP BY pu.id ORDER BY pu.picked_at DESC LIMIT 100""",
            (client["id"],),
        ).fetchall()
        if db.postgres
        else db.execute(
            """SELECT pu.*,MIN(pp.package_id) first_package,COUNT(*) own_count,SUM(COALESCE(pp.prepaid_amount,0)) own_prepaid,SUM(pp.base_amount) own_base,SUM(pp.late_amount) own_late,SUM(CASE WHEN EXISTS(SELECT 1 FROM plan_credit_ledger l WHERE l.package_id=pp.package_id) THEN 1 ELSE 0 END) covered_count,(SELECT COUNT(*) FROM pickup_packages allp WHERE allp.pickup_id=pu.id) all_count FROM pickups pu JOIN pickup_packages pp ON pp.pickup_id=pu.id WHERE pp.client_id=? GROUP BY pu.id ORDER BY pu.picked_at DESC LIMIT 100""",
            (client["id"],),
        ).fetchall()
    )
    warnings = []
    from datetime import datetime

    for raw in db.execute(
        "SELECT * FROM packages WHERE client_id=? AND status!='retirado' AND notified_at IS NOT NULL AND substr(notified_at,1,10)<=? ORDER BY notified_at LIMIT 50",
        (client["id"], (value.date() - timedelta(days=1)).isoformat()),
    ).fetchall():
        day = (
            value.date()
            - datetime.fromisoformat(raw["notified_at"]).astimezone(value.tzinfo).date()
        ).days + 1
        if day >= 3:
            warnings.append(
                {
                    **dict(raw),
                    **fees(raw),
                    "deadline_alert": "late" if day >= 5 else "ending",
                    "deadline": (
                        datetime.fromisoformat(raw["notified_at"])
                        .astimezone(value.tzinfo)
                        .date()
                        + timedelta(days=3)
                    ).strftime("%d/%m/%Y"),
                }
            )
    return dict(
        warnings=warnings,
        customer_tabs=TABS,
        selected_tab=selected,
        selected_title=dict((k, v) for k, v, _ in TABS)[selected],
        counts=counts,
        available_credits=available,
        current=current,
        future=future,
        cycles=history,
        orders=db.execute(
            "SELECT * FROM plan_orders WHERE client_id=? AND state!='historical_entitlement' ORDER BY created_at DESC LIMIT 50",
            (client["id"],),
        ).fetchall(),
        ledger=db.execute(
            "SELECT l.*,p.public_id FROM plan_credit_ledger l JOIN plan_cycles c ON c.id=l.cycle_id LEFT JOIN packages p ON p.id=l.package_id WHERE c.client_id=? ORDER BY l.id DESC LIMIT 100",
            (client["id"],),
        ).fetchall(),
        withdrawals=withdrawals,
        contract_history=records(db, client),
        google_identity=db.execute(
            "SELECT email_at_link,linked_at FROM google_client_identities WHERE client_id=?",
            (client["id"],),
        ).fetchone(),
        labels=plan_routes.LABELS,
        plan_rules=plans.offered_rules(db),
        card_ready=False,
        embedded_plan=True,
        logo=False,
    )
