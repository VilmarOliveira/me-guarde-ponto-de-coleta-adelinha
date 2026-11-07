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

folder = Path(tempfile.mkdtemp(prefix="adelinha-payment-order-"))
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
server = make_server("127.0.0.1", 5089, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:5089"
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

    def methods():
        return page.locator("[data-payment-row]").evaluate_all(
            "rows=>rows.map(r=>r.dataset.method)"
        )

    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(origin + "/painel/configuracoes/pagamentos-cliente")
        page.wait_for_load_state("networkidle")
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
        tab_x = page.locator(".section-tabs a").first.bounding_box()["x"]
        content_x = page.locator(".payment-settings h1").bounding_box()["x"]
        assert abs(tab_x - content_x) < 2, (width, tab_x, content_x)
        heights = page.locator(".section-tabs a").evaluate_all(
            "links=>links.map(e=>e.getBoundingClientRect().height)"
        )
        assert max(heights) - min(heights) < 1
        assert page.locator(".payment-method-name").all_text_contents() == [
            "Pix Fast",
            "Pix AI",
            "Dinheiro",
        ]
        for field in page.locator(
            ".payment-order-table input[inputmode=decimal]"
        ).all():
            assert field.is_visible()
            assert field.evaluate(
                "e=>{const s=getComputedStyle(e),c=document.createElement('canvas').getContext('2d');c.font=s.font;return c.measureText(e.value).width<=e.clientWidth-parseFloat(s.paddingLeft)-parseFloat(s.paddingRight)+1}"
            ), (width, field.get_attribute("name"))
        if width <= 800:
            assert (
                page.locator("[data-payment-row]").first.evaluate(
                    "e=>getComputedStyle(e).display"
                )
                == "grid"
            )
        else:
            assert (
                page.locator(".payment-order-table thead th").first.evaluate(
                    "e=>getComputedStyle(e).backgroundColor"
                )
                == "rgb(232, 244, 252)"
            )
        assert (
            page.locator("[name=enabled_0]").evaluate(
                "e=>getComputedStyle(e.nextElementSibling).backgroundColor"
            )
            == "rgb(21, 128, 61)"
        )
    page.locator("[name=max_0]").fill("500.00")
    ai = page.locator('[data-method="Pix AI"]')
    fast = page.locator('[data-method="Pix Fast"]')
    ai.locator(".payment-grip").drag_to(fast)
    assert methods() == ["Pix AI", "Pix Fast", "Dinheiro"]
    assert (
        page.locator("[name=priority_1]").input_value() == "1"
        and page.locator("[name=max_0]").input_value() == "500.00"
    )
    page.get_by_role("switch", name="Ativar Pix Fast", exact=True).uncheck()
    up = page.get_by_role("button", name="Subir Dinheiro", exact=True)
    up.focus()
    page.keyboard.press("Enter")
    up.focus()
    page.keyboard.press("Enter")
    assert methods() == ["Dinheiro", "Pix AI", "Pix Fast"]
    assert page.locator("[name=priority_2]").input_value() == "1"
    page.get_by_role("button", name="Salvar alterações", exact=True).click()
    page.wait_for_load_state("networkidle")
    page.reload()
    assert (
        methods() == ["Dinheiro", "Pix AI", "Pix Fast"]
        and not page.locator("[name=enabled_0]").is_checked()
    )
    assert page.locator("[name=max_0]").input_value() == "500.00"
    page.get_by_role("link", name="Contratação do plano", exact=True).click()
    assert (
        methods() == ["Pix Fast", "Pix AI", "Dinheiro"]
        and page.locator("[name=max_0]").input_value() == "1000.00"
    )
    page.get_by_role("button", name="Subir Pix AI", exact=True).click()
    page.get_by_role("button", name="Salvar alterações", exact=True).click()
    page.reload()
    assert methods() == ["Pix AI", "Pix Fast", "Dinheiro"]
    page.get_by_role("link", name="Renovação do plano", exact=True).click()
    assert methods() == ["Pix Fast", "Pix AI", "Dinheiro"]
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(100)
    touch = browser.new_context(
        is_mobile=True, has_touch=True, viewport={"width": 390, "height": 844}
    )
    cookie(touch, web)
    phone = touch.new_page()
    phone.on("pageerror", lambda e: errors.append(str(e)))
    phone.goto(origin + "/painel/configuracoes/pagamentos-cliente?aba=renewal")
    phone.wait_for_load_state("networkidle")
    phone.get_by_role("button", name="Subir Dinheiro", exact=True).tap()
    phone.get_by_role("button", name="Salvar alterações", exact=True).tap()
    phone.reload()
    assert phone.locator("[data-payment-row]").evaluate_all(
        "rows=>rows.map(r=>r.dataset.method)"
    ) == ["Pix Fast", "Dinheiro", "Pix AI"]
    assert phone.evaluate("document.documentElement.scrollWidth<=innerWidth")
    phone.get_by_role("link", name="Pagamento antecipado", exact=True).tap()
    assert (
        phone.locator("[data-payment-row]").first.get_attribute("data-method")
        == "Dinheiro"
    )
    phone.screenshot(path=str(folder / "payment-order-390.png"), full_page=True)
    assert fake.creates == 1
    browser.close()
server.shutdown()
db.close()
assert not errors, errors
print(
    json.dumps(
        {
            "widths": [320, 360, 390, 430, 768, 1024, 1440],
            "full_values": True,
            "mouse_drag": True,
            "keyboard": True,
            "touch": True,
            "independent_save_reload": True,
            "js_errors": errors,
            "artifacts": str(folder),
        }
    )
)
