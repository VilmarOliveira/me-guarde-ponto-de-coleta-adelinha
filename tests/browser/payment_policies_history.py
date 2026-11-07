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

folder = Path(tempfile.mkdtemp(prefix="adelinha-policy-history-"))
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
from datetime import timedelta
from test_customer_payments import deliver

current = datetime.now(TZ)
for number, days, hour in [(3, 1, 23), (4, 0, 1), (5, 0, 2)]:
    package(db, number=number)
    assert deliver(web, [number], "Dinheiro").status_code == 302
    db.execute(
        "UPDATE pickups SET picked_at=? WHERE id=?",
        (
            (current - timedelta(days=days))
            .replace(hour=hour, minute=0, second=0, microsecond=0)
            .isoformat(),
            number - 2,
        ),
    )
    db.commit()
server = make_server("127.0.0.1", 5083, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:5083"
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
    user = app.test_client()
    with user.session_transaction() as s:
        s.update(client_id=1, client_session_version=1)
    cookie(ctx, user)
    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(100)
        page.goto(origin + "/cliente?aba=plano")
        page.wait_for_load_state("networkidle")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
            width,
            page.evaluate(
                "[...document.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).slice(0,12).map(e=>[e.tagName,e.className,e.getAttribute('name'),e.getBoundingClientRect().right])"
            ),
        )
        assert page.locator(".saving-percent").all_text_contents() == ["30,8%", "31,5%"]
        assert page.locator(".plan-savings table").bounding_box()["width"] <= width
    page.screenshot(path=str(folder / "savings.png"), full_page=True)
    cookie(ctx, web)
    page.goto(origin + "/painel")
    page.wait_for_load_state("networkidle")
    retired = page.locator(".package-grid>div").last
    assert retired.locator("article.package").count() == 2
    assert (
        "Retirada #3" in retired.inner_text()
        and "Retirada #1" not in retired.inner_text()
    )
    page.get_by_role("link", name="Ver histórico completo", exact=True).click()
    assert page.locator("[name=filtro]").input_value() == "hoje"
    assert page.locator("article.package").count() == 2
    page.locator("[name=filtro]").select_option("dia")
    page.locator("[name=dia]").fill((current.date() - timedelta(days=1)).isoformat())
    page.get_by_role("button", name="Filtrar", exact=True).click()
    assert (
        page.locator("article.package").count() == 1
        and "Retirada #1" in page.locator("article.package").inner_text()
    )
    page.locator("[name=filtro]").select_option("mes")
    page.locator("[name=mes]").fill(current.strftime("%Y-%m"))
    page.get_by_role("button", name="Filtrar", exact=True).click()
    assert page.locator("article.package").count() == 3
    page.locator("[name=filtro]").select_option("periodo")
    page.locator("[name=inicio]").fill((current.date() - timedelta(days=1)).isoformat())
    page.locator("[name=fim]").fill(current.date().isoformat())
    page.get_by_role("button", name="Filtrar", exact=True).click()
    assert page.locator("article.package").count() == 3
    page.locator("[name=filtro]").select_option("todas")
    page.get_by_role("button", name="Filtrar", exact=True).click()
    assert page.locator("article.package").count() == 3
    page.get_by_role("link", name="Limpar filtros", exact=True).click()
    assert page.locator("[name=filtro]").input_value() == "hoje"
    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(100)
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
            width,
            page.evaluate(
                "[...document.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).slice(0,12).map(e=>[e.tagName,e.className,e.getAttribute('name'),e.getBoundingClientRect().right])"
            ),
        )
    page.get_by_role("link", name="Voltar", exact=True).click()
    page.wait_for_url("**/painel")
    page.goto(origin + "/painel/configuracoes/pagamentos-cliente")
    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(100)
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
            width,
            page.evaluate(
                "[...document.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).slice(0,12).map(e=>[e.tagName,e.className,e.getAttribute('name'),e.getBoundingClientRect().right])"
            ),
        )
    page.locator("[name=max_0]").fill("500")
    page.locator("[name=test_value]").fill("600")
    page.get_by_role("button", name="Prévia sem cobrança", exact=True).click()
    assert "Pagar com Pix AI" in page.locator("aside.notice").inner_text()
    page.get_by_role("button", name="Salvar alterações", exact=True).click()
    page.get_by_role("link", name="Contratação do plano", exact=True).click()
    assert page.locator("[name=max_0]").input_value() == "1000.00"
    page.get_by_role("link", name="Renovação do plano", exact=True).click()
    assert page.locator("[name=max_0]").input_value() == "1000.00"
    page.locator("[name=min_0]").fill("40")
    page.get_by_role("button", name="Salvar alterações", exact=True).click()
    page.get_by_role("link", name="Pagamento antecipado", exact=True).click()
    assert (
        page.locator("[name=max_0]").input_value() == "500.00"
        and page.locator("[name=min_0]").input_value() == "4.00"
    )
    cookie(ctx, user)
    page.goto(origin + "/cliente?aba=plano")
    assert "Pagar com Pix AI" in page.locator(".plan-checkout").inner_text()
    assert page.locator(".plan-checkout button").count() == 1
    assert fake.creates == 1
    browser.close()
server.shutdown()
db.close()
assert not errors, errors
print(
    json.dumps(
        {
            "widths": [320, 360, 390, 430, 768, 1024, 1440],
            "savings": True,
            "today_and_filters": True,
            "independent_tabs": True,
            "preview_no_charge": True,
            "js_errors": errors,
            "artifacts": str(folder),
        }
    )
)
