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

folder = Path(tempfile.mkdtemp(prefix="adelinha-prepaid-"))
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
server = make_server("127.0.0.1", 5081, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:5081"
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
    ctx = browser.new_context(accept_downloads=True)
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    customer = app.test_client()
    with customer.session_transaction() as s:
        s.update(client_id=1, client_session_version=1)
    cookie(ctx, customer)
    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(origin + "/cliente?aba=pacotes")
        page.wait_for_load_state("networkidle")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
    page.locator('[name=package_ids][value="1"]').check()
    page.locator('[name=package_ids][value="2"]').check()
    page.get_by_role("button", name="Pagar antes da retirada", exact=True).click()
    assert "Incluído no plano · Nada a pagar" in page.locator("main").inner_text()
    page.get_by_role("button", name="Gerar Pix Fast", exact=True).click()
    page.wait_for_url("**/cliente/pacotes/pagamentos/*")
    order = db.execute("SELECT * FROM pickup_payment_orders").fetchone()
    assert order["advance"] == 1 and order["amount_cents"] == 490
    assert page.locator("#advance-code").input_value().startswith("000201")
    for width in (320, 390, 768, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
    pay(ctx, order["id"], order["transaction_id"], "retiradas")
    page.reload()
    assert (
        "Pago antecipadamente · Aguardando retirada"
        in page.locator("main").inner_text()
    )
    assert db.execute("SELECT COUNT(*) n FROM pickups").fetchone()["n"] == 0
    assert db.execute("SELECT COUNT(*) n FROM package_prepayments").fetchone()["n"] == 2
    page.goto(origin + "/cliente?aba=pacotes")
    assert (
        "Pago antecipadamente · Aguardando retirada"
        in page.locator("main").inner_text()
    )
    cookie(ctx, web)
    page.goto(origin + "/painel")
    page.wait_for_load_state("networkidle")
    page.locator('[name=package_ids][value="1"]').check()
    page.locator('[name=package_ids][value="2"]').check()
    page.locator("[name=payment_method][value=Dinheiro]").check()
    page.wait_for_function(
        "document.querySelector('#pickup-total').textContent==='R$ 0,00'"
    )
    page.locator("[name=receiver_name]").fill("Retirada ficticia")
    page.locator("[name=document_type]").select_option("CPF")
    page.locator("[name=receiver_document]").fill("52998224725")
    canvas = page.locator("canvas.signature")
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    page.mouse.move(box["x"] + 20, box["y"] + 30)
    page.mouse.down()
    page.mouse.move(box["x"] + 100, box["y"] + 60, steps=8)
    page.mouse.up()
    page.locator("[name=payment_confirmed]").check()
    page.get_by_role("button", name="Confirmar retirada", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert "Pedido retirado com sucesso!" in page.locator("main").inner_text()
    assert db.execute("SELECT total_paid FROM pickups").fetchone()["total_paid"] == 0
    assert (
        db.execute("SELECT used_credits FROM plan_cycles").fetchone()["used_credits"]
        == 1
    )
    with page.expect_download() as download:
        page.get_by_role("link", name="Baixar comprovante em PDF", exact=True).first.click()
    download.value.save_as(str(folder / "prepaid-receipt.pdf"))
    browser.close()
server.shutdown()
db.close()
assert not errors, errors
print(
    json.dumps(
        {
            "widths": [320, 360, 390, 430, 768, 1024, 1440],
            "customer_prepayment": True,
            "zero_pickup": True,
            "pdf": True,
            "js_errors": errors,
            "artifacts": str(folder),
        }
    )
)
