"""Fictional prices/users, mocked API, immutable previously issued charges."""

import pytest
from datetime import datetime
from test_plan import site, post_purchase
from test_customer_payments import payments, staff, admin_form
from test_advance_payments import customer
from test_customization import legacy_login
from test_app import token
import plan_service as ps
import plan_pricing as pricing
from core import TZ


def change(client, value):
    legacy_login(client)
    return client.post(
        "/painel/configuracoes/plano/mensalidade",
        data={"monthly_price": value, "csrf_token": token(client)},
        follow_redirects=True,
    )




@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "1.234", "", "abc"])
def test_invalid_monthly_value_does_not_change_price(payments, value):
    _, client, db, fake = payments
    assert change(client, value).status_code == 200
    assert pricing.current(db) == 3390 and fake.creates == 0


def test_new_offer_public_comparison_and_stale_checkout(payments):
    _, client, db, fake = payments
    change(client, "39.90")
    customer(client)
    page = client.get("/cliente?aba=plano").text
    assert "R$ 39,90" in page and "R$ 3,99" in page and "R$ 7,98" in page
    assert "18,6%" in page and "19,4%" in page
    assert "R$ 39,90" in client.get("/").text
    response = client.post(
        "/cliente/plano/assinar",
        data={"plan_rules": "yes", "price_cents": "3390", "csrf_token": token(client)},
    )
    assert (
        response.status_code == 302
        and not db.execute("SELECT 1 FROM plan_orders").fetchone()
        and fake.creates == 0
    )
    response = client.post(
        "/cliente/plano/assinar",
        data={"plan_rules": "yes", "price_cents": "3990", "csrf_token": token(client)},
    )
    assert (
        response.status_code == 302
        and db.execute("SELECT amount_cents FROM plan_orders").fetchone()[
            "amount_cents"
        ]
        == 3990
    )


def test_manual_old_order_payment_and_new_administrative_amount(payments):
    _, client, db, fake = payments
    # Existing manual order is still paid at its captured amount.
    from test_payment_policies_history import configure

    configure(db, "subscription", **{"Pix Fast": {"enabled": False}})
    post_purchase(client)
    order = db.execute("SELECT * FROM plan_orders").fetchone()
    change(client, "49.90")
    path = "/painel/configuracoes/plano/pedidos/" + order["id"] + "/approve"
    assert client.post(path, data=admin_form(client, path, "Pix AI")).status_code == 302
    record = db.execute(
        "SELECT * FROM plan_admin_decisions WHERE action=?", ("approve",)
    ).fetchone()
    assert record["amount_cents"] == 3390
    path = "/painel/configuracoes/plano/clientes/1/validar"
    assert (
        client.post(path, data=admin_form(client, path, "Dinheiro")).status_code == 302
    )
    nextorder = db.execute(
        "SELECT * FROM plan_orders WHERE id!=?", (order["id"],)
    ).fetchone()
    assert nextorder["amount_cents"] == 4990 and fake.creates == 0


def test_navigation_separates_public_and_internal_and_permissions(payments):
    app, client, db, fake = payments
    customer(client)
    internal = (
        client.get("/cliente")
        .text.split('<nav id="main-nav"', 1)[1]
        .split("</nav>", 1)[0]
    )
    assert (
        "Dúvidas" not in internal
        and "> Blog" not in internal
        and "Cadastre-se" not in internal
    )
    assert "Sair" in internal
    assert client.get("/painel").status_code == 302
    assert (
        client.post(
            "/painel/configuracoes/plano/mensalidade",
            data={"monthly_price": "1", "csrf_token": token(client)},
        ).status_code
        == 302
    )
    sid = db.execute(
        "INSERT INTO staff_users(name,email,password_hash,role,can_settings,can_packages,can_pickups,created_at) VALUES('Atendente fixture','price-staff@example.test','fixture','atendente',0,1,1,?) RETURNING id",
        (datetime.now(TZ).isoformat(),),
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True, staff_user_id=sid, staff_session_version=1)
    panel = (
        client.get("/painel")
        .text.split('<nav id="main-nav"', 1)[1]
        .split("</nav>", 1)[0]
    )
    assert "Dúvidas" not in panel and "Gerenciar blog" not in panel and "Sair" in panel
    assert client.get("/painel/blog").status_code == 403
    assert (
        client.post(
            "/painel/configuracoes/plano/mensalidade",
            data={"monthly_price": "1", "csrf_token": token(client)},
        ).status_code
        == 403
    )
    legacy_login(client)
    panel = (
        client.get("/painel")
        .text.split('<nav id="main-nav"', 1)[1]
        .split("</nav>", 1)[0]
    )
    assert "Gerenciar blog" not in panel and "Dúvidas" not in panel
    assert "Gerenciar blog" in client.get("/painel/configuracoes").text
    guest = app.test_client()
    public = guest.get("/").text
    assert (
        "Dúvidas" in public
        and "Cadastre-se" in public
        and 'href="/cliente/login"' in public
        and 'href="/login"' in public
    )
    assert pricing.current(db) == 3390




@pytest.mark.parametrize("receive,withdraw", [(1, 0), (0, 1)])
def test_operation_actions_match_profile_and_direct_permissions(
    payments, receive, withdraw
):
    _, client, db, _ = payments
    sid = db.execute(
        "INSERT INTO staff_users(name,email,password_hash,role,can_clients,can_packages,can_pickups,created_at) VALUES(?,?,?,?,1,?,?,?) RETURNING id",
        (
            "Perfil ficticio",
            "scope-%s@example.test" % receive,
            "fixture",
            "atendente",
            receive,
            withdraw,
            datetime.now(TZ).isoformat(),
        ),
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True, staff_user_id=sid, staff_session_version=1)
    page = client.get("/painel").text
    assert ('id="package-form"' in page) == bool(receive)
    assert ('id="pickup-form"' in page) == bool(withdraw)
    if not receive:
        assert (
            client.post("/pacotes", data={"csrf_token": token(client)}).status_code
            == 403
        )
    if not withdraw:
        assert (
            client.post("/retiradas", data={"csrf_token": token(client)}).status_code
            == 403
        )
