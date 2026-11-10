"""Requested navigation/registration and financial archival; all payments mocked."""

import re
import pytest
from test_plan import site, post_purchase
from test_customer_payments import (
    payments,
    package,
    staff,
    create_pickup,
    admin_form,
)
from test_advance_payments import customer, advance
from test_customization import legacy_login
from test_app import token
from test_plan import CONFIG


def test_home_registration_map_and_six_customer_tabs(payments):
    app, client, db, _ = payments
    home = app.test_client().get("/").text
    assert 'id="registration-form"' not in home
    assert "FAZER CADASTRO AGORA" in home and 'href="/cadastro"' in home
    assert "Conheça o serviço" not in home and ">Preços e planos</a>" not in home
    assert "https://maps.app.goo.gl/5rCTnEns2EDe9RhV6" in home
    assert "Cadastre-se em nosso site" in home
    assert (
        "Antes de fazer sua compra, crie sua conta no site do Ponto de Coleta Adelinha."
        not in home
    )
    registration = client.get("/cadastro").text
    assert (
        'id="registration-form"' in registration and "Voltar ao início" in registration
    )
    assert (
        "residential_street" in registration
        and "confirmation_code" in registration
        and 'name="signature"' in registration
    )
    assert "residencial é separado" in registration
    customer(client)
    area = client.get("/cliente").text
    nav = area.split('class="customer-tabs"', 1)[1].split("</nav>", 1)[0]
    labels = [
        "Visão geral",
        "Meus pacotes",
        "Meu plano",
        "Histórico",
        "Meus dados",
        "Contratos e autorizações",
    ]
    assert [nav.index(label) for label in labels] == sorted(
        nav.index(label) for label in labels
    )
    assert '<details class="customer-area-menu"' not in area


def test_staff_menu_and_settings_task_cards(payments):
    _, client, db, _ = payments
    legacy_login(client)
    page = client.get("/painel").text
    nav = page.split('<nav id="main-nav"', 1)[1].split("</nav>", 1)[0]
    assert (
        nav.index("Painel de métricas") < nav.index("Configurações") < nav.index("Sair")
    )
    assert (
        "Pacotes e retiradas" not in nav
        and "Gerenciar blog" not in nav
        and "> Histórico" not in nav
    )
    hub = client.get("/painel/configuracoes").text
    assert (
        "Pacotes e retiradas" in hub
        and "Gerenciar blog" in hub
        and "Histórico</b>" in hub
    )
    assert "Plano ME GUARDE+</b>" in hub and "Plano ME GUARDE Mais</b>" not in hub
    sid = db.execute(
        "INSERT INTO staff_users(name,email,password_hash,role,can_settings,can_users,can_packages,can_pickups,created_at) VALUES('Operador ficticio','eleven-crew@example.test','fixture','atendente',0,0,1,1,'2026-11-07T10:00:00-03:00') RETURNING id"
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True, staff_user_id=sid, staff_session_version=1)
    assert client.get("/painel/configuracoes").status_code == 200
    hub = client.get("/painel/configuracoes").text
    assert (
        "Pacotes e retiradas" in hub
        and "Histórico</b>" in hub
        and "Gerenciar blog" not in hub
    )
    assert client.get("/painel/blog").status_code == 403
