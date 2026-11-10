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

folder = Path(tempfile.mkdtemp(prefix="adelinha-eleven-updates-"))
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
    session.update(staff=True, staff_legacy=True)
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
for date, device in [
    ("2026-10-01T10:00:00-03:00", "mobile"),
    ("2026-10-02T10:00:00-03:00", "desktop"),
]:
    db.execute(
        "INSERT INTO analytics_events(visitor_id,event_type,path,origin,device,created_at) VALUES(?,'pageview','/','direta',?,?)",
        (device, device, date),
    )
db.commit()
server = make_server("127.0.0.1", 5095, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:5095"
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
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    guest = app.test_client()
    guest.get("/")
    user = app.test_client()
    with user.session_transaction() as s:
        s.update(client_id=1, client_session_version=1)
    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 1000})
        cookie(ctx, guest)
        page.goto(origin + "/")
        assert page.locator("#registration-form").count() == 0
        assert (
            page.get_by_role("link", name="Como chegar", exact=True).get_attribute(
                "href"
            )
            == "https://maps.app.goo.gl/5rCTnEns2EDe9RhV6"
        )
        page.get_by_role("link", name="FAZER CADASTRO AGORA", exact=True).click()
        assert page.url.endswith("/cadastro")
        assert page.locator("#registration-form").is_visible()
        assert page.locator('[name="signature"]').count() == 1
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
            width,
            "registration",
        )
        cookie(ctx, user)
        for tab in (
            "visao-geral",
            "pacotes",
            "plano",
            "historico",
            "dados",
            "contratos",
        ):
            page.goto(origin + "/cliente?aba=" + tab)
            assert page.locator(".customer-tabs a").count() == 6
            for link in page.locator(".customer-tabs a").all():
                assert link.is_visible()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
                width,
                tab,
            )
            if tab=="visao-geral":
                page.screenshot(path=str(folder/(str(width)+"-customer.png")),full_page=True)
        cookie(ctx, web)
        for path in (
            "/painel",
            "/painel/configuracoes",
            "/painel/configuracoes/identidade",
            "/painel/metricas?period=custom&start=2026-10-01&end=2026-10-02",
        ):
            response = page.goto(origin + path)
            assert response.status == 200, (path, response.status)
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
                width,
                path,
            )
        if page.locator(".nav-toggle").is_visible():
            page.locator(".nav-toggle").click()
            assert page.get_by_role("link", name="Configurações",exact=True).is_visible()
            page.locator(".nav-toggle").click()
        links = page.locator("#main-nav").text_content()
        assert "Gerenciar blog" not in links and "Pacotes e retiradas" not in links
        assert (
            links.index("Painel de métricas")
            < links.index("Configurações")
            < links.index("Sair")
        )
        assert page.locator(".chart-column").count() == 2
        assert page.get_by_role("link",name="Barras",exact=True).bounding_box()["height"]<90
        page.screenshot(path=str(folder/(str(width)+"-bars.png")),full_page=True)
        assert (
            page.locator(".chart-y-axis").bounding_box()["x"]
            < page.locator(".chart-columns").bounding_box()["x"]
        )
        page.get_by_role("link", name="Pizza", exact=True).click()
        assert (
            "start=2026-10-01" in page.url
            and page.locator(".chart-column").count() == 0
        )
        assert page.locator(".metrics-chart svg").is_visible()
        page.screenshot(
            path=str(folder / (str(width) + "-metrics.png")), full_page=True
        )
    cookie(ctx, web)
    page.goto(origin + "/painel/configuracoes")
    page.get_by_role("link", name="Pacotes e retiradas").click()
    assert page.url.endswith("/painel")
    from test_customer_payments import create_pickup

    order_id = create_pickup(web, [pid]).json["id"]
    order = dict(
        db.execute(
            "SELECT * FROM pickup_payment_orders WHERE id=?", (order_id,)
        ).fetchone()
    )
    page.goto(origin + "/painel/retirada/pix-fast/" + order_id)
    page.get_by_role("button", name="Arquivar cobrança", exact=True).click()
    assert order_id not in page.content()
    page.get_by_role("link", name="Histórico de cobranças arquivadas").click()
    assert order_id in page.content()
    cookie(ctx, user)
    page.goto(origin + "/cliente/pacotes/pagamentos/" + order_id)
    assert "Cobrança arquivada" in page.locator("main").inner_text()
    assert page.locator("#advance-code,#pickup-pix").count() == 0
    pay(ctx, order_id, order["transaction_id"], "retiradas")
    cookie(ctx, web)
    page.goto(origin + "/painel/retirada/cobrancas?arquivadas=1")
    assert "Pagamento após arquivamento" in page.locator("main").inner_text()
    assert not db.execute("SELECT 1 FROM pickups").fetchone()
    assert not db.execute("SELECT 1 FROM package_prepayments").fetchone()
    assert not errors, errors
    browser.close()
server.shutdown()
print(
    "PASS: seven widths; public registration/map, six customer tabs, staff navigation, vertical/pie charts and archived late payment; mocked API only. Screenshots:",
    folder,
)
