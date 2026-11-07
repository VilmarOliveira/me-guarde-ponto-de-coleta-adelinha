"""Pickup dates use Franca's America/Sao_Paulo zone, including inclusive ranges."""

from datetime import datetime, timedelta, date
from core import TZ


def interval(args, today):
    mode = args.get("filtro", "hoje")
    if mode == "todas":
        return mode, None, None
    if mode == "hoje":
        start = end = today
    elif mode == "dia":
        start = end = date.fromisoformat(args.get("dia", ""))
    elif mode == "mes":
        start = date.fromisoformat(args.get("mes", "") + "-01")
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(
            days=1
        )
    elif mode == "periodo":
        start = date.fromisoformat(args.get("inicio", ""))
        end = date.fromisoformat(args.get("fim", ""))
    else:
        raise ValueError("Filtro inválido.")
    if start > end:
        raise ValueError("A data inicial deve ser anterior ou igual à final.")
    return (
        mode,
        datetime.combine(start, datetime.min.time(), TZ),
        datetime.combine(end + timedelta(days=1), datetime.min.time(), TZ),
    )


def rows(db, args, today):
    mode, start, end = interval(args, today)
    # Historical rows may carry different ISO offsets. Convert the pickup instant,
    # rather than comparing timestamp strings lexically or using arrival dates.
    found = []
    for row in db.execute(
        "SELECT pu.id,pu.picked_at,pu.receiver_name,pu.payment_method,pu.total_paid,(SELECT MIN(package_id) FROM pickup_packages pp WHERE pp.pickup_id=pu.id) package_id FROM pickups pu"
    ).fetchall():
        value = datetime.fromisoformat(row["picked_at"])
        value = (
            value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)
        )
        if start is None or start <= value < end:
            found.append((value, dict(row)))
    found.sort(key=lambda x: (x[0], x[1]["id"]), reverse=True)
    return mode, [r for _, r in found]
