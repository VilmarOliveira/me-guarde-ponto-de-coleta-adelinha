"""Accessible chart data from the existing pageview metrics, without third parties."""

from collections import Counter
import math
from datetime import datetime
from core import TZ

COLORS = ("#e64a00", "#087f83", "#324b60", "#9a6400", "#6c5745")


def build(events, devices, monthly=False):
    counts = Counter()
    for event in events:
        value = datetime.fromisoformat(event["created_at"])
        value = (
            value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)
        )
        counts[value.strftime("%Y-%m" if monthly else "%Y-%m-%d")] += 1
    bars = [
        {
            "label": datetime.strptime(
                key, "%Y-%m" if monthly else "%Y-%m-%d"
            ).strftime("%m/%Y" if monthly else "%d/%m/%Y"),
            "count": value,
        }
        for key, value in sorted(counts.items())
    ]
    maximum = max(counts.values(), default=1)
    axis_max = max(4, math.ceil(maximum / 4) * 4)
    for bar in bars:
        bar["height"] = 100 * bar["count"] / axis_max
        bar["width"] = 100 * bar["count"] / maximum
    total = sum(row["total"] for row in devices)
    segments = []
    offset = 0
    labels = {"mobile": "Celular", "desktop": "Computador", "tablet": "Tablet"}
    for i, row in enumerate(
        sorted(devices, key=lambda x: (-x["total"], str(x["device"])))
    ):
        if not row["total"]:
            continue
        share = 100 * row["total"] / total
        segments.append(
            {
                "label": labels.get(row["device"], row["device"] or "Não identificado"),
                "count": row["total"],
                "share": share,
                "offset": offset,
                "color": COLORS[i % len(COLORS)],
            }
        )
        offset += share
    return {
        "ticks": [int(axis_max * (4 - i) / 4) for i in range(5)],
        "bars": bars,
        "segments": segments,
        "total": total,
        "monthly": monthly,
    }
