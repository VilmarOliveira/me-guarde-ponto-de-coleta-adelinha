"""Customer routing and history use fictional data, never real payment APIs."""

import json
from datetime import datetime, timedelta
import pytest
from test_plan import site, post_purchase
from test_customer_payments import payments, package, staff, deliver, admin_form
from test_advance_payments import advance, customer, paid
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


@pytest.mark.parametrize(
    "amount,method",
    [
        (0, "zero"),
        (399, "Pix AI"),
        (400, "Pix Fast"),
        (100000, "Pix Fast"),
        (100001, "Pix AI"),
        (10000000, "Pix AI"),
        (10000001, "Dinheiro"),
    ],
)
def test_inclusive_routing_without_changing_values(payments, amount, method):
    app, _, db, fake = payments
    assert policies.select(db, app, "advance", amount)["method"] == method
    assert fake.creates == 0


def test_admin_tabs_preview_persist_independently(payments):
    app, client, db, fake = payments
    assert client.get("/painel/configuracoes/pagamentos-cliente").status_code == 302
    staff(client)
    rules = policies.rules(db, "advance")
    rules[0]["maximum"] = 50000
    url = "/painel/configuracoes/pagamentos-cliente"
    response = client.post(url, data=form(client, rules, action="preview"))
    assert response.status_code == 200 and "Pagar com Pix AI" in response.text
    assert policies.rules(db, "advance")[0]["maximum"] == 100000
    assert client.post(url, data=form(client, rules)).status_code == 302
    assert policies.rules(db, "advance")[0]["maximum"] == 50000
    assert policies.rules(db, "subscription")[0]["maximum"] == 100000
    assert policies.rules(db, "renewal")[0]["maximum"] == 100000
    assert policies.select(db, app, "advance", 60000)["method"] == "Pix AI"
    assert fake.creates == 0
    bad = form(client, rules)
    bad["min_0"] = "600"
    bad["max_0"] = "500"
    assert "mínimo" in client.post(url, data=bad).text
    assert policies.rules(db, "advance")[0]["maximum"] == 50000
    sid = db.execute(
        "INSERT INTO staff_users(name,email,password_hash,role,can_settings,created_at) VALUES('Gerente ficticio','manager@example.test','fixture','gerente',1,?) RETURNING id",
        (datetime.now(TZ).isoformat(),),
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True, staff_user_id=sid, staff_session_version=1)
    assert client.get(url).status_code == 403
    assert client.post(url, data=form(client, rules)).status_code == 403


def test_fallback_ai_manual_confirmation_and_pending_snapshot(payments):
    app, client, db, fake = payments
    configure(db, **{"Pix Fast": {"maximum": 50000}})
    pid = package(db, base=600)
    response = advance(client, [pid])
    oid = response.json["id"]
    row = db.execute("SELECT * FROM pickup_payment_orders").fetchone()
    assert (
        row["local_method"] == "Pix AI"
        and row["amount_cents"] == 60000
        and row["qr_text"].startswith("000201")
    )
    assert (
        fake.creates == 0
        and not db.execute("SELECT 1 FROM package_prepayments").fetchone()
    )
    configure(db, **{"Pix AI": {"enabled": False}, "Pix Fast": {"enabled": False}})
    assert advance(client, [pid]).json["id"] == oid
    assert (
        db.execute("SELECT local_method FROM pickup_payment_orders").fetchone()[
            "local_method"
        ]
        == "Pix AI"
    )
    path = "/painel/retirada/pix-fast/" + oid + "/confirmar-manual"
    assert (
        client.post(
            path, data={"payment_confirmed": "yes", "csrf_token": token(client)}
        ).status_code
        == 302
    )
    staff(client)
    assert client.post(path, data={"csrf_token": token(client)}).status_code == 400
    assert (
        client.post(
            path, data={"payment_confirmed": "yes", "csrf_token": token(client)}
        ).status_code
        == 302
    )
    client.post(path, data={"payment_confirmed": "yes", "csrf_token": token(client)})
    assert db.execute("SELECT COUNT(*) n FROM package_prepayments").fetchone()["n"] == 1
    assert (
        db.execute("SELECT paid_cents FROM package_prepayments").fetchone()[
            "paid_cents"
        ]
        == 60000
    )
    assert fake.creates == 0
    deliver(client, [pid], "Dinheiro")
    assert db.execute("SELECT total_paid FROM pickups").fetchone()["total_paid"] == 0


def test_pending_fast_never_switches_even_when_rules_disabled(payments):
    app, client, db, fake = payments
    pid = package(db)
    oid = advance(client, [pid]).json["id"]
    db.execute("UPDATE pickup_payment_orders SET state='uncertain' WHERE id=?", (oid,))
    db.commit()
    configure(db, **{"Pix Fast": {"enabled": False}})
    assert advance(client, [pid]).json["id"] == oid
    assert (
        "Verificando cobrança" in client.get("/cliente/pacotes/pagamentos/" + oid).text
    )
    assert fake.creates == 1
    staff(client)
    assert (
        client.post(
            "/painel/retirada/pix-fast/" + oid + "/confirmar-manual",
            data={"payment_confirmed": "yes", "csrf_token": token(client)},
        ).status_code
        == 403
    )
    assert not db.execute("SELECT 1 FROM package_prepayments").fetchone()


def test_cash_when_fast_offline_and_ai_unavailable(payments):
    app, client, db, fake = payments
    app.config["FASTFLOW_ENABLED"] = "0"
    db.execute("UPDATE settings SET pix_key_type='EMAIL'")
    db.commit()
    pid = package(db)
    oid = advance(client, [pid]).json["id"]
    assert (
        db.execute("SELECT local_method FROM pickup_payment_orders").fetchone()[
            "local_method"
        ]
        == "Dinheiro"
    )
    assert (
        "Pagar em dinheiro no ponto"
        in client.get("/cliente/pacotes/pagamentos/" + oid).text
    )
    assert fake.creates == 0


def test_plan_subscription_ai_and_renewal_fast_remain_independent(payments):
    _, client, db, fake = payments
    configure(db, "subscription", **{"Pix Fast": {"minimum": 4000}})
    response = post_purchase(client)
    order = db.execute("SELECT * FROM plan_orders").fetchone()
    assert order["method"] == "administrative" and order["local_method"] == "Pix AI"
    assert order["amount_cents"] == 3390 and fake.creates == 0
    assert order["accepted_at"] and not order["paid_at"]
    assert "Nenhum crédito" in client.get(response.location).text
    assert not db.execute("SELECT 1 FROM plan_cycles").fetchone()
    staff(client)
    path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/approve"
    response = client.post(path, data=admin_form(client, path, "Pix AI"))
    assert response.status_code == 302
    cycle = db.execute("SELECT * FROM plan_cycles").fetchone()
    assert cycle and cycle["granted_at"]
    customer(client)
    fake.state = "paid"
    post_purchase(client)
    orders = db.execute("SELECT * FROM plan_orders ORDER BY created_at,id").fetchall()
    assert len(orders) == 2 and fake.creates == 1
    future = db.execute(
        "SELECT * FROM plan_cycles WHERE order_id!=?", (order["id"],)
    ).fetchone()
    assert future["starts_at"] == cycle["ends_at"] and not future["granted_at"]
    assert (
        db.execute(
            "SELECT ends_at FROM plan_cycles WHERE id=?", (cycle["id"],)
        ).fetchone()["ends_at"]
        == cycle["ends_at"]
    )


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
    fake.state = "paid"
    post_purchase(client)
    page = client.get("/cliente?aba=plano").text
    assert page.index("Quanto você economiza?") < page.index("Meu ciclo atual")
    assert (
        "30,8%" in page and "31,5%" in page and "R$ 3,39" in page and "R$ 6,78" in page
    )
    assert (
        db.execute("SELECT amount_cents FROM plan_orders").fetchone()["amount_cents"]
        == 3390
    )


def test_definitive_fast_failure_falls_back_but_ambiguous_does_not(
    payments, monkeypatch
):
    _, client, db, fake = payments
    from fastflow_gateway import GatewayError

    pid = package(db)
    attempts = []

    def rejected(*args, **kwargs):
        attempts.append(1)
        raise GatewayError(code="rejected")

    monkeypatch.setattr(fake, "create", rejected)
    first = advance(client, [pid]).json["id"]
    assert (
        db.execute(
            "SELECT state FROM pickup_payment_orders WHERE id=?", (first,)
        ).fetchone()["state"]
        == "failed"
    )
    page = client.post(
        "/cliente/pacotes/pagar",
        data={"package_ids": [pid], "csrf_token": token(client)},
    )
    assert "Pagar com Pix AI" in page.text
    second = advance(client, [pid]).json["id"]
    assert second != first and len(attempts) == 1
    assert (
        db.execute(
            "SELECT local_method FROM pickup_payment_orders WHERE id=?", (second,)
        ).fetchone()["local_method"]
        == "Pix AI"
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


def test_config_priority_and_api_hard_bounds(payments):
    app, _, db, fake = payments
    configure(db, **{"Pix Fast": {"maximum": 10000000}})
    assert policies.select(db, app, "advance", 500000)["method"] == "Pix Fast"
    assert policies.select(db, app, "advance", 500001)["method"] == "Pix AI"
    configure(db, **{"Dinheiro": {"priority": 1}, "Pix Fast": {"priority": 3}})
    assert policies.select(db, app, "advance", 3390)["method"] == "Dinheiro"
    assert (
        policies.select(db, app, "advance", 0)["method"] == "zero" and fake.creates == 0
    )


def test_policies_survive_restart_and_manual_cancel_does_not_call_api(
    payments, monkeypatch
):
    app, client, db, fake = payments
    configure(db, **{"Pix Fast": {"maximum": 50000}})
    from app import create_app
    from database import DatabaseConnection

    restarted = create_app({**app.config, "TESTING": True})
    other = DatabaseConnection(restarted.config["DATABASE"])
    assert policies.rules(other, "advance")[0]["maximum"] == 50000
    other.close()
    configure(db, "subscription", **{"Pix Fast": {"enabled": False}})
    post_purchase(client)
    order = db.execute("SELECT * FROM plan_orders").fetchone()
    assert order["local_method"] == "Pix AI"
    staff(client)
    path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/cancel"
    monkeypatch.setattr(
        "plan_routes.gateway", lambda _: pytest.fail("No API for a local Pix AI order")
    )
    assert client.post(path, data=admin_form(client, path, "Pix AI")).status_code == 302
    assert (
        db.execute("SELECT state FROM plan_orders").fetchone()["state"]
        == "admin_cancelled"
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
