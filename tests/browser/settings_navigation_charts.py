"""Isolated responsive/customer/three-method browser fixture; no real API/email."""

import sys, tempfile, threading, json, hmac, hashlib, uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from datetime import datetime
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from app import create_app
from database import DatabaseConnection
from core import TZ
from test_app import register, token
from test_plan import CONFIG, FakeFlow
import plan_routes, plan_service as ps
from fastflow_gateway import GatewayError

folder = Path(tempfile.mkdtemp(prefix="adelinha-settings-charts-"))
app = create_app({**CONFIG, "DATABASE": str(folder / "fixture.db")})
web = app.test_client()
register(web)
db = DatabaseConnection(app.config["DATABASE"])
db.execute(
    "UPDATE clients SET confirmation_channel='email',confirmation_destination=email,confirmation_validated_at=accepted_at"
)
db.execute(
    "UPDATE settings SET pix_receiver_name='Titular fixture',pix_key_type='CNPJ',pix_key='38145273000105'"
)
db.commit()
fake = FakeFlow()
old = fake.create
fake.cancel = lambda tid: fake.records[str(tid)].update(status="cancelled")


def create(order, owner, card=None):
    data = old(order, owner, card)
    fake.records[data["id"]]["amount"] = order["amount_cents"] / 100
    return fake.records[data["id"]].copy()


fake.create = create
plan_routes.gateway = lambda _: fake
# Existing package received before the plan, followed by a plan-covered receipt.
from test_customer_payments import package

pid = package(db)
with web.session_transaction() as session:
    session.update(client_id=1, client_session_version=1)
fake.state = "paid"
web.post("/cliente/plano/assinar", data={"plan_rules": "yes", "csrf_token": token(web)})
fake.state = "pending"
with web.session_transaction() as session:
    session.clear()
    session.update(staff=True)
web.post(
    "/pacotes",
    data={
        "client_id": "1",
        "tracking": "COVERED2",
        "width": "10",
        "height": "10",
        "length": "10",
        "weight": "1",
        "shelf": "A2",
        "csrf_token": token(web),
    },
)
web.post("/pacotes/2/avisar", data={"csrf_token": token(web)})
from test_customization import legacy_login

legacy_login(web)
from datetime import timedelta

for i, device in enumerate(["mobile", "mobile", "desktop"]):
    stamp = (datetime.now(TZ) - timedelta(days=i % 2)).isoformat()
    db.execute(
        "INSERT INTO analytics_events(visitor_id,event_type,path,origin,device,created_at) VALUES(?,'pageview','/','direta',?,?)",
        (str(i), device, stamp),
    )
db.commit()
server = make_server("127.0.0.1", 5085, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:5085"
errors = []


def cookie(ctx, client):
    value = client.get_cookie("session")
    ctx.clear_cookies()
    ctx.add_cookies(
        [{"name": "session", "value": value.value, "domain": "127.0.0.1", "path": "/"}]
    )


def pay(ctx, oid, tid, purpose):
    fake.records[str(tid)]["status"] = "paid"
    raw = json.dumps({"transaction_id": str(tid), "status": "paid"}).encode()
    sig = hmac.new(
        CONFIG["FASTFLOW_WEBHOOK_SECRET"].encode(), raw, hashlib.sha256
    ).hexdigest()
    result = ctx.request.post(
        origin + "/webhooks/fastflow/" + purpose + "/" + oid,
        data=raw,
        headers={
            "X-Webhook-Signature": "sha256=" + sig,
            "Content-Type": "application/json",
        },
    )
    assert result.status == 200


with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"]
    )
    ctx = browser.new_context()
    cookie(ctx, web)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(origin + "/painel/configuracoes")
        page.wait_for_load_state("networkidle")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
        columns = page.locator(".settings-hub-grid").evaluate(
            "e=>getComputedStyle(e).gridTemplateColumns.split(' ').length"
        )
        assert columns == (4 if width > 1100 else 2 if width > 600 else 1), (
            width,
            columns,
        )
        cards = page.locator(".settings-hub-grid .settings-card")
        assert cards.count() == 8
        geometry = cards.evaluate_all(
            "items=>items.map(e=>({y:Math.round(e.getBoundingClientRect().y),height:e.getBoundingClientRect().height,width:e.getBoundingClientRect().width,decoration:getComputedStyle(e).textDecorationLine}))"
        )
        assert max(c["width"] for c in geometry) - min(c["width"] for c in geometry) < 1
        for y in set(c["y"] for c in geometry):
            heights = [c["height"] for c in geometry if c["y"] == y]
            assert max(heights) - min(heights) < 1
        assert all(c["decoration"] == "none" for c in geometry)
    cards.first.focus()
    page.keyboard.press("Tab")
    assert (
        page.locator(".settings-card:focus").evaluate(
            "e=>getComputedStyle(e).outlineStyle"
        )
        != "none"
    )
    for width in (390, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.get_by_role(
            "link",
            name="Identidade visual Personalize as imagens e a aparência do site.",
        ).click()
        for text in [
            "Imagem do cabeçalho",
            "Banners",
            "Fotos do local",
            "Aparência",
            "Cartões explicativos",
        ]:
            page.locator(".section-tabs").get_by_role(
                "link", name=text, exact=True
            ).click()
            page.wait_for_load_state("networkidle")
            assert (
                page.locator(".section-tabs [aria-current=page]").inner_text() == text
            )
            assert page.evaluate(
                "document.documentElement.scrollWidth<=innerWidth"
            ), text
        page.locator(".section-tabs").get_by_role(
            "link", name="Aparência", exact=True
        ).click()
        page.get_by_role("button", name="Restaurar cores padrão", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert (
            page.locator(".section-tabs [aria-current=page]").inner_text()
            == "Aparência"
        )
        page.locator(".section-tabs").get_by_role(
            "link", name="Voltar para Configurações", exact=True
        ).click()
        page.get_by_role("link", name="Pagamentos Ordem, limites e chave Pix.").click()
        assert (
            page.locator(".section-tabs [aria-current=page]").inner_text()
            == "Ordem e limites"
        )
        page.get_by_role("link", name="Renovação do plano", exact=True).click()
        page.get_by_role("button", name="Salvar alterações", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert "aba=renewal" in page.url
        page.locator(".section-tabs").get_by_role(
            "link", name="Chave Pix", exact=True
        ).click()
        page.get_by_role("link", name="Editar dados do Pix", exact=True).click()
        page.locator("[name=receiver]").fill("Titular fixture atualizado")
        page.get_by_role("button", name="Salvar alterações", exact=True).click()
        page.wait_for_load_state("networkidle")
        assert (
            page.locator(".section-tabs [aria-current=page]").inner_text()
            == "Chave Pix"
        )
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        page.locator(".section-tabs").get_by_role(
            "link", name="Voltar para Configurações", exact=True
        ).click()
    chart_start = (datetime.now(TZ).date() - timedelta(days=6)).isoformat()
    chart_end = datetime.now(TZ).date().isoformat()
    for width in (320, 390, 768, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(
            origin
            + f"/painel/metricas?period=custom&start={chart_start}&end={chart_end}"
        )
        page.wait_for_load_state("networkidle")
        assert (
            page.locator(".metrics-chart [aria-current=page]").inner_text() == "Barras"
        )
        assert (
            page.locator(".chart-row").count() > 0
            and page.locator(".metrics-chart circle").count() == 0
        )
        page.locator(".metrics-chart").get_by_role(
            "link", name="Pizza", exact=True
        ).click()
        page.wait_for_load_state("networkidle")
        assert f"start={chart_start}" in page.url and f"end={chart_end}" in page.url
        assert (
            page.locator(".chart-row").count() == 0
            and page.locator(".metrics-chart circle").count() == 2
        )
        assert "66,7%" in page.locator(".chart-legend").inner_text()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    page.goto(
        origin
        + "/painel/metricas?period=custom&start=2000-01-01&end=2000-01-02&graph=pie"
    )
    assert (
        "Sem dados para o período selecionado"
        in page.locator(".metrics-chart").inner_text()
    )
    assert fake.creates == 1
    browser.close()
server.shutdown()
db.close()
assert not errors, errors
print(
    json.dumps(
        {
            "widths": [320, 360, 390, 430, 768, 1024, 1440],
            "cards_4_2_1": True,
            "equal_rows": True,
            "keyboard_focus": True,
            "tabs_and_save": True,
            "charts_and_period": True,
            "js_errors": errors,
            "artifacts": str(folder),
        }
    )
)
