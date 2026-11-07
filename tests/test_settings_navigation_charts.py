"""Unified settings reuse existing routes; chart counts come from real fixtures."""

from datetime import datetime, timedelta
import json
import re
import pytest
from test_plan import site
from test_customer_payments import payments, staff, package, deliver
from test_app import token
from test_customization import legacy_login as staff
from core import TZ
from metrics_charts import build

IDENTITY = [
    ("/painel/configuracoes/identidade", "Imagem do cabeçalho"),
    ("/painel/configuracoes/banners", "Banners"),
    ("/painel/configuracoes/fotos", "Fotos do local"),
    ("/painel/configuracoes/aparencia", "Aparência"),
    ("/painel/configuracoes/cartoes-explicativos", "Cartões explicativos"),
]


def test_settings_cards_consolidated_and_history_primary(payments):
    _, client, db, _ = payments
    staff(client)
    page = client.get("/painel/configuracoes").text
    assert len(re.findall(r"\sIdentidade visual</b>", page)) == 1
    assert "Personalize as imagens e a aparência do site." in page
    assert "Pagamentos</b>" in page
    for path, _ in IDENTITY[1:]:
        assert f'href="{path}"' not in page
    assert 'href="/painel/configuracoes/pix"' not in page
    pid = package(db)
    deliver(client, [pid], "Dinheiro")
    dash = client.get("/painel").text
    assert (
        'class="button primary" href="/painel/retiradas/historico">Ver histórico completo'
        in dash
    )
    assert (
        '<option value="hoje" selected>Hoje</option>'
        in client.get("/painel/retiradas/historico").text
    )


@pytest.mark.parametrize("path,label", IDENTITY)
def test_identity_tabs_and_selected_endpoint(payments, path, label):
    _, client, _, _ = payments
    staff(client)
    response = client.get(path)
    assert response.status_code == 200
    nav = response.text.split('class="section-tabs actions"', 1)[1].split("</nav>", 1)[
        0
    ]
    positions = [nav.index(text) for _, text in IDENTITY]
    assert positions == sorted(positions)
    assert f'href="{path}" aria-current="page">{label}</a>' in nav
    assert "Voltar para Configurações" in nav


def test_save_does_not_leave_selected_section_or_erase_another(payments):
    _, client, db, _ = payments
    staff(client)
    db.execute(
        "UPDATE settings SET pix_key_type='CNPJ',pix_key='38145273000105',pix_receiver_name='Titular fixture'"
    )
    db.commit()
    # Existing appearance restore acts only on its own setting, preserves Pix.
    response = client.post(
        "/painel/configuracoes/aparencia",
        data={"action": "restore", "csrf_token": token(client)},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert 'href="/painel/configuracoes/aparencia" aria-current="page"' in response.text
    assert (
        db.execute("SELECT pix_key FROM settings").fetchone()["pix_key"]
        == "38145273000105"
    )
    from test_payment_policies_history import form
    from payment_policies import rules

    data = form(client, rules(db, "renewal"), scope="renewal")
    data["max_0"] = "500"
    response = client.post(
        "/painel/configuracoes/pagamentos-cliente", data=data, follow_redirects=True
    )
    assert (
        response.status_code == 200
        and 'href="/painel/configuracoes/pagamentos-cliente?aba=renewal"'
        in response.text
    )
    assert (
        rules(db, "renewal")[0]["maximum"] == 50000
        and rules(db, "advance")[0]["maximum"] == 100000
    )
    assert (
        'href="/painel/configuracoes/pagamentos-cliente" aria-current="page">Ordem e limites'
        in response.text
    )
    pix = client.get("/painel/configuracoes/pix").text
    assert 'href="/painel/configuracoes/pix" aria-current="page">Chave Pix' in pix
    assert "Voltar para Configurações" in pix


def test_permissions_preserved_for_settings_tabs(payments):
    _, client, db, _ = payments
    assert client.get("/painel/configuracoes/identidade").status_code == 302
    sid = db.execute(
        "INSERT INTO staff_users(name,email,password_hash,role,can_settings,created_at) VALUES('Gerente ficticio','tabs-manager@example.test','fixture','gerente',1,?) RETURNING id",
        (datetime.now(TZ).isoformat(),),
    ).fetchone()["id"]
    db.commit()
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True, staff_user_id=sid, staff_session_version=1)
    assert client.get("/painel/configuracoes/pix").status_code == 403
    assert client.get("/painel/configuracoes/pagamentos-cliente").status_code == 403
    assert client.get("/painel/configuracoes/cartoes-explicativos").status_code == 403
    identity = client.get("/painel/configuracoes/identidade").text
    assert ">Cartões explicativos</a>" not in identity


def test_graphs_use_same_pageview_total_and_preserve_period(payments):
    _, client, db, _ = payments
    staff(client)
    for date, device in [
        ("2026-10-01T10:00:00-03:00", "mobile"),
        ("2026-10-01T11:00:00-03:00", "mobile"),
        ("2026-10-02T12:00:00-03:00", "desktop"),
    ]:
        db.execute(
            "INSERT INTO analytics_events(visitor_id,event_type,path,origin,device,created_at) VALUES(?,'pageview','/','direta',?,?)",
            (device, device, date),
        )
    db.execute(
        "INSERT INTO analytics_events(visitor_id,event_type,path,origin,device,created_at) VALUES('click','whatsapp','/','direta','mobile','2026-10-01T11:00:00-03:00')"
    )
    db.commit()
    path = "/painel/metricas?period=custom&start=2026-10-01&end=2026-10-02"
    bars = client.get(path).text
    assert (
        "Visitas por dia" in bars
        and 'class="chart-row"' in bars
        and '<circle cx="50"' not in bars
    )
    assert "period=custom&amp;start=2026-10-01&amp;end=2026-10-02&amp;graph=pie" in bars
    pie = client.get(path + "&graph=pie").text
    assert 'class="chart-row"' not in pie and '<circle cx="50"' in pie
    assert (
        "Celular — 2 visita(s) — 66,7%" in pie
        and "Computador — 1 visita(s) — 33,3%" in pie
    )
    assert 'name="graph" value="pie"' in pie
    empty = client.get(
        "/painel/metricas?period=custom&start=2000-01-01&end=2000-01-02&graph=pie"
    ).text
    assert (
        "Sem dados para o período selecionado" in empty
        and '<circle cx="50"' not in empty
    )
    assert (
        client.get(
            "/painel/metricas?period=custom&start=2026-10-02&end=2026-10-01"
        ).status_code
        == 400
    )


def test_monthly_bar_aggregation():
    chart = build(
        [
            {"created_at": "2026-01-01T10:00:00-03:00"},
            {"created_at": "2026-01-02T10:00:00-03:00"},
            {"created_at": "2026-02-01T10:00:00-03:00"},
        ],
        [{"device": "mobile", "total": 3}],
        monthly=True,
    )
    assert [(row["label"], row["count"]) for row in chart["bars"]] == [
        ("01/2026", 2),
        ("02/2026", 1),
    ]
    assert sum(row["count"] for row in chart["segments"]) == 3 and chart["total"] == 3
