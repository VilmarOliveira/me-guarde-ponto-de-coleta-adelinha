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

folder = Path(tempfile.mkdtemp(prefix="adelinha-eight-updates-"))
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
db.execute(
    "INSERT INTO google_client_identities VALUES('browser-google',1,'cliente@example.com',1)"
)
from io import BytesIO
from PIL import Image

out = BytesIO()
Image.new("RGB", (2048, 684), "orange").save(out, "PNG")
image = out.getvalue()
for position in (2, 3):
    db.execute(
        "INSERT INTO site_banners(position,image,mime_type,alt_text,updated_at,active) VALUES(?,?,'image/png','Fixture','browser-v1',1)",
        (position, image),
    )
db.commit()
server = make_server("127.0.0.1", 5097, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:5097"
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
    with user.session_transaction() as session:
        session.update(client_id=1, client_session_version=1)
    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        cookie(ctx, guest)
        page.goto(origin + "/")
        page.bring_to_front()
        page.locator(".brand").hover()
        assert (
            page.get_by_role(
                "heading", name="Cadastre-se em nosso site", exact=True
            ).count()
            == 1
        )
        assert "Antes de fazer sua compra, crie sua conta" not in page.content()
        page.wait_for_function("document.querySelector('[data-slide] img').complete")
        assert (
            page.locator("[data-slide]").nth(2).locator("img").get_attribute("src")
            is None
        )
        try:
            page.wait_for_function(
                "document.querySelector('[data-slide].active').getAttribute('aria-label')==='2 de 3'",
                timeout=12000,
            )
        except Exception:
            print(
                "CAROUSEL",
                width,
                page.evaluate(
                    "({focus:document.hasFocus(),hidden:document.hidden,hover:document.querySelector('[data-carousel]').matches(':hover'),active:document.activeElement.tagName,images:[...document.querySelectorAll('[data-slide] img')].map(i=>({complete:i.complete,src:i.currentSrc}))})"
                ),
            )
            raise
        page.locator('[data-slide-to="2"]').click()
        page.wait_for_function(
            "document.querySelector('[data-slide].active').getAttribute('aria-label')==='3 de 3'"
        )
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
        if page.locator(".nav-toggle").is_visible():
            page.locator(".nav-toggle").click()
        page.get_by_role("link", name="LOGIN", exact=True).click()
        assert page.url.endswith("/cliente/login")
        cookie(ctx, user)
        page.goto(origin + "/")
        if page.locator(".nav-toggle").is_visible():
            page.locator(".nav-toggle").click()
        page.get_by_role("link", name="LOGIN", exact=True).click()
        assert "/cliente" in page.url
        page.goto(origin + "/cliente?aba=plano")
        assert "ME GUARDE Mais" not in page.locator("main").inner_text()
        assert page.get_by_role(
            "button", name="RENOVAR PLANO AGORA", exact=False
        ).is_visible()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
        page.goto(origin + "/cliente?aba=dados")
        assert page.get_by_role(
            "link", name="Desvincular conta Google", exact=True
        ).is_visible()
        page.get_by_role("link", name="Desvincular conta Google", exact=True).click()
        page.get_by_role("link", name="Cancelar", exact=True).click()
        assert db.execute("SELECT 1 FROM google_client_identities").fetchone()
        page.screenshot(path=str(folder / (str(width) + "-data.png")), full_page=True)
    page.get_by_role("link", name="Desvincular conta Google", exact=True).click()
    page.locator("[name=password]").fill("senha-segura")
    page.locator("[name=confirm]").check()
    page.get_by_role("button", name="Desvincular conta Google", exact=True).click()
    assert not db.execute("SELECT 1 FROM google_client_identities").fetchone()
    user = app.test_client()
    with user.session_transaction() as session:
        session.update(client_id=1, client_session_version=2)
    cookie(ctx, user)
    response = user.post(
        "/cliente/plano/assinar", data={"plan_rules": "yes", "csrf_token": token(user)}
    )
    oid = response.location.rsplit("/", 1)[-1]
    row = dict(db.execute("SELECT * FROM plan_orders WHERE id=?", (oid,)).fetchone())
    from datetime import timedelta

    expiry = (datetime.now(TZ) + timedelta(seconds=4)).isoformat()
    fake.records[row["transaction_id"]]["qr_code_expires_at"] = expiry
    db.execute(
        "UPDATE plan_orders SET expires_at=?,check_after=NULL WHERE id=?", (expiry, oid)
    )
    db.commit()
    page.goto(origin + response.location)
    assert page.locator("[data-plan-pix]").is_visible()
    page.wait_for_timeout(4500)
    assert not page.locator("[data-plan-pix]").is_visible()
    assert "O prazo do QR Code terminou." in page.locator("main").inner_text()
    fake.records[row["transaction_id"]]["status"] = "expired"
    db.execute("UPDATE plan_orders SET check_after=NULL WHERE id=?", (oid,))
    db.commit()
    page.get_by_role("button", name="Consultar pagamento", exact=True).click()
    page.get_by_role("button", name="Gerar novo Pix", exact=True).click()
    new_id = page.url.rsplit("/", 1)[-1]
    assert new_id != oid
    assert (
        db.execute(
            "SELECT root_order_id FROM plan_orders WHERE id=?", (new_id,)
        ).fetchone()["root_order_id"]
        == oid
    )
    assert page.locator("[data-plan-pix]").is_visible()
    new = dict(db.execute("SELECT * FROM plan_orders WHERE id=?", (new_id,)).fetchone())
    pay(ctx, new_id, new["transaction_id"], "pedidos")
    page.get_by_role("button", name="Consultar pagamento", exact=True).click()
    assert "Pagamento aprovado!" in page.locator("main").inner_text()
    assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()["n"] == 2
    assert not errors, errors
    browser.close()
server.shutdown()
print(
    "PASS: seven widths, LOGIN guest/authenticated, deferred carousel and controls, Google unlink/cancel, renewal and expired Pix retry/payment. API/email mocked. Screenshots:",
    folder,
)
