"""Customer routing and history use fictional data, never real payment APIs."""

import json
from datetime import datetime, timedelta
import pytest
from test_plan import site, post_purchase
from test_customer_payments import payments, package, staff, deliver, admin_form
from test_advance_payments import advance, customer
from test_app import token
from core import TZ
import payment_policies as policies
import plan_service as plans
import pickup_history


def configure(db, scope="advance", **changes):
    rules = policies.rules(db, scope)
    for rule in rules:
        if rule["method"] in changes:
            rule.update(changes[rule["method"]])
    db.execute(
        "UPDATE customer_payment_policies SET rules=? WHERE scope=?",
        (json.dumps(rules), scope),
    )
    db.commit()
    return rules


def form(client, rules, scope="advance", action="save"):
    data = {
        "csrf_token": token(client),
        "aba": scope,
        "action": action,
        "test_value": "600",
    }
    for i, r in enumerate(rules):
        data.update(
            {
                f"min_{i}": str(r["minimum"] / 100),
                f"max_{i}": str(r["maximum"] / 100),
                f"priority_{i}": str(r["priority"]),
                f"title_{i}": r["title"],
                f"text_{i}": r["text"],
            }
        )
        if r["enabled"]:
            data[f"enabled_{i}"] = "yes"
    return data














def test_today_history_filters_and_two_latest_pickups(payments):
    _, client, db, _ = payments
    staff(client)
    today = datetime.now(TZ).date()
    stamps = [
        datetime.combine(today - timedelta(days=1), datetime.min.time(), TZ)
        .replace(hour=23, minute=59)
        .isoformat(),
        datetime.combine(today, datetime.min.time(), TZ)
        .replace(hour=1)
        .astimezone(__import__("datetime").timezone.utc)
        .isoformat(),
        datetime.combine(today, datetime.min.time(), TZ)
        .replace(hour=23, minute=59)
        .isoformat(),
    ]
    for i, stamp in enumerate(stamps):
        pid = package(db, number=i + 1)
        deliver(client, [pid], "Dinheiro")
        db.execute("UPDATE pickups SET picked_at=? WHERE id=?", (stamp, i + 1))
        db.commit()
    page = client.get("/painel/retiradas/historico").text
    assert "Retirada #3" in page and "Retirada #2" in page and "Retirada #1" not in page
    assert page.index("Retirada #3") < page.index("Retirada #2")
    dash = client.get("/painel").text
    assert "Retirada #3" in dash and "Retirada #2" in dash and "Retirada #1" not in dash
    assert "Ver histórico completo" in dash
    for args, expected in [
        ({"filtro": "dia", "dia": (today - timedelta(days=1)).isoformat()}, [1]),
        (
            {
                "filtro": "periodo",
                "inicio": (today - timedelta(days=1)).isoformat(),
                "fim": today.isoformat(),
            },
            [3, 2, 1],
        ),
        (
            {"filtro": "mes", "mes": today.strftime("%Y-%m")},
            [3, 2, 1] if today.day > 1 else [3, 2],
        ),
        ({"filtro": "todas"}, [3, 2, 1]),
    ]:
        _, rows = pickup_history.rows(db, args, today)
        assert [r["id"] for r in rows] == expected
    future = (today + timedelta(days=100)).isoformat()
    assert (
        "Nenhuma retirada encontrada no período"
        in client.get(
            "/painel/retiradas/historico", query_string={"filtro": "dia", "dia": future}
        ).text
    )
    assert (
        "Confira as datas"
        in client.get("/painel/retiradas/historico?filtro=dia&dia=invalid").text
    )
    assert 'href="/painel/retiradas/historico"' in page and 'href="/painel"' in page


def test_savings_order_and_original_prices(payments):
    _, client, db, fake = payments
    from test_plan import create_cycle
    create_cycle(db,value=datetime.now(TZ))
    page = client.get("/cliente?aba=plano").text
    assert page.index("Quanto você economiza?") < page.index("Meu ciclo atual")
    assert (
        "30,8%" in page and "31,5%" in page and "R$ 3,39" in page and "R$ 6,78" in page
    )
    assert (
        db.execute("SELECT amount_cents FROM plan_orders").fetchone()["amount_cents"]
        == 3390
    )




def test_manual_cash_plan_requires_admin_and_keeps_pending_snapshot(payments):
    _, client, db, fake = payments
    configure(
        db,
        "subscription",
        **{"Pix Fast": {"enabled": False}, "Pix AI": {"enabled": False}},
    )
    post_purchase(client)
    order = db.execute("SELECT * FROM plan_orders").fetchone()
    assert (
        order["local_method"] == "Dinheiro"
        and not db.execute("SELECT 1 FROM plan_cycles").fetchone()
    )
    configure(db, "subscription", **{"Dinheiro": {"enabled": False}})
    response = post_purchase(client)
    assert response.location.endswith(order["id"]) and fake.creates == 0
    assert "Pagar em dinheiro no ponto" in client.get(response.location).text
    configure(db, "subscription", **{"Pix Fast": {"enabled": True}})
    assert (
        client.post(
            "/painel/configuracoes/plano/pedidos/" + order["id"] + "/approve",
            data={"confirm": "yes", "csrf_token": token(client)},
        ).status_code
        == 302
    )
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone()






def test_no_eligible_method_creates_no_charge(payments):
    _, client, db, fake = payments
    configure(db, **{method: {"enabled": False} for method in policies.METHODS})
    pid = package(db)
    assert advance(client, [pid]).status_code == 400
    assert (
        not db.execute("SELECT 1 FROM pickup_payment_orders").fetchone()
        and fake.creates == 0
    )


def test_history_permission_and_detail_for_pickup_only_staff(payments):
    _, client, db, _ = payments
    pid = package(db)
    staff(client)
    deliver(client, [pid], "Dinheiro")
    sid = db.execute(
        "INSERT INTO staff_users(name,email,password_hash,role,can_packages,can_pickups,created_at) VALUES('Retirada ficticia','pickup-only@example.test','fixture','atendente',0,1,?) RETURNING id",
        (datetime.now(TZ).isoformat(),),
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True, staff_user_id=sid, staff_session_version=1)
    assert client.get("/painel/retiradas/historico").status_code == 200
    assert client.get("/painel/retiradas/historico/1").status_code == 200
    db.execute("UPDATE staff_users SET can_pickups=0 WHERE id=?", (sid,))
    db.commit()
    assert client.get("/painel/retiradas/historico").status_code == 403
    assert client.get("/painel/retiradas/historico/1").status_code == 403
