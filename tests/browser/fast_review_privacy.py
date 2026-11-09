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

folder = Path(tempfile.mkdtemp(prefix="adelinha-fast-review-"))
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
server = make_server("127.0.0.1", 5102, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:5102"
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
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    user = app.test_client()
    with user.session_transaction() as s:
        s.update(client_id=1, client_session_version=1)
    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        for authenticated in (False, True):
            ctx.clear_cookies()
            if authenticated:
                cookie(ctx, user)
            for path in ("/", "/cadastro", "/cliente/login", "/privacidade"):
                response = page.goto(origin + path)
                assert response.status == 200
                assert (
                    page.locator('a[href="/cliente/pendencias-e-autorizacoes"]').count()
                    == 0
                ), (width, path)
                assert page.evaluate(
                    "document.documentElement.scrollWidth<=innerWidth"
                ), (width, path)
        cookie(ctx, user)
        page.goto(origin + "/cliente/editar")
        pending = page.get_by_role(
            "link", name="Pendências e autorizações (0)", exact=True
        )
        assert pending.is_visible()
        pending.click()
        assert page.url.endswith("/cliente/pendencias-e-autorizacoes")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        cookie(ctx, web)
        response = page.goto(origin + "/painel/configuracoes/pendencias")
        assert response.status == 200
        # Staff management routes/permissions remain independent of the customer notice.
        assert page.locator('a[href="/cliente/pendencias-e-autorizacoes"]').count() == 0
        cookie(ctx, user)
        page.goto(origin + "/cliente?aba=contratos")
        assert page.get_by_role(
            "link", name="Pendências e autorizações (0)", exact=True
        ).is_visible()
        if page.locator(".nav-toggle").is_visible():
            page.locator(".nav-toggle").click()
        page.get_by_role("button", name="Sair", exact=True).click()
        page.goto(origin + "/")
        assert page.locator('a[href="/cliente/pendencias-e-autorizacoes"]').count() == 0
        page.screenshot(
            path=str(folder / (str(width) + "-public-after-logout.png")), full_page=True
        )
    cookie(ctx, user)
    page.goto(origin + "/cliente?aba=plano")
    app.config["FASTFLOW_NEW_CHARGES_ENABLED"] = "0"
    page.reload()
    assert "Pagar com Pix AI" in page.locator("main").inner_text()
    assert "RENOVAR PLANO AGORA" not in page.locator("main").inner_text()
    assert not errors, errors
    browser.close()
server.shutdown()
print(
    "PASS: seven widths, public/login/logout notices absent, authenticated counter/destination preserved, staff separation and AI fallback during Fast review. No real payments. Screenshots:",
    folder,
)
