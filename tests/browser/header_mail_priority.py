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

folder = Path(tempfile.mkdtemp(prefix="adelinha-header-mail-"))
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
server = make_server("127.0.0.1", 5101, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:5101"
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


import app as application

mail = {"accepted": True, "calls": 0}


def send(channel, email, code):
    mail["calls"] += 1
    return mail["accepted"], (
        "Gmail API recusou a solicitação (403)"
        if not mail["accepted"]
        else "gmail-fixture-accepted"
    )


application.send_code = send
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"]
    )
    ctx = browser.new_context()
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    user = app.test_client()
    with user.session_transaction() as session:
        session.update(client_id=1, client_session_version=1)
    for width in (320, 360, 390, 430, 768, 1024, 1440):
        ctx.clear_cookies()
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(origin + "/")
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(100)
        compact = page.locator(".site-header").evaluate(
            "e=>e.classList.contains('nav-compact')"
        )
        if compact:
            actions = page.locator(".public-mobile-actions")
            labels = actions.locator("a,button").all_inner_texts()
            assert [x.strip() for x in labels] == ["Cadastre-se", "LOGIN", "Menu"], (
                width,
                labels,
            )
            boxes = actions.locator("a,button").evaluate_all(
                "es=>es.map(e=>{const r=e.getBoundingClientRect();return {y:r.y,height:r.height,x:r.x,width:r.width}})"
            )
            if max(b["y"] for b in boxes) - min(b["y"] for b in boxes) >= 2:
                page.locator(".site-header").screenshot(
                    path=str(folder / "failure.png")
                )
                print("HEADER", width, boxes, folder, flush=True)
            assert max(b["y"] for b in boxes) - min(b["y"] for b in boxes) < 2
            for link in actions.locator("a").all():
                assert (
                    link.evaluate("e=>getComputedStyle(e).color")
                    == "rgb(255, 255, 255)"
                )
                assert link.evaluate("e=>e.scrollWidth<=e.clientWidth")
            page.get_by_role("button", name="Menu", exact=True).click()
            links = page.locator("#main-nav a:visible")
            assert [x.strip() for x in links.all_inner_texts()] == [
                "Dúvidas",
                "Blog",
                "Área do Cliente",
                "Área do Ponto",
            ]
            boxes = [link.bounding_box() for link in links.all()]
            assert all(
                boxes[i + 1]["y"] >= boxes[i]["y"] + boxes[i]["height"]
                for i in range(3)
            )
        else:
            assert [
                x.strip() for x in page.locator("#main-nav a:visible").all_inner_texts()
            ] == ["Dúvidas", "Blog", "Cadastre-se", "Login Cliente", "Área do Ponto"]
            assert (
                page.get_by_role("link", name="Login Cliente", exact=True).evaluate(
                    "e=>getComputedStyle(e).color"
                )
                == "rgb(255, 255, 255)"
            )
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
        page.locator(".site-header").screenshot(
            path=str(folder / (str(width) + "-header.png"))
        )
        page.goto(origin + "/cadastro")
        page.locator("[name=cpf]").fill("52998224725")
        page.locator("[name=email]").fill("vilmar@gmail.com")
        page.locator("[name=name]").fill("Nome Fictício Preservado")
        page.locator("#accept").check()
        old = mail["calls"]
        page.locator("#send-registration-code").click()
        page.wait_for_function(
            "document.querySelector('#registration-code-status').textContent.startsWith('Enviamos um código')"
        )
        assert mail["calls"] == old + 1
        assert "v***@g***.com" in page.locator("#registration-code-status").inner_text()
        assert (
            page.locator("[name=name]").input_value() == "Nome Fictício Preservado"
            and page.locator("#accept").is_checked()
        )
        assert (
            page.locator("#registration-code-status").bounding_box()["y"]
            >= page.locator("#send-registration-code").bounding_box()["y"]
        )
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
    ctx.clear_cookies()
    mail["accepted"] = False
    page.goto(origin + "/cadastro")
    page.locator("[name=cpf]").fill("52998224725")
    page.locator("[name=email]").fill("vilmar@gmail.com")
    page.locator("#send-registration-code").click()
    page.wait_for_function(
        "document.querySelector('#registration-code-status').textContent.includes('Não foi possível')"
    )
    assert (
        "Enviamos um código"
        not in page.locator("#registration-code-status").inner_text()
    )
    assert not page.locator("#send-registration-code").is_disabled()
    cookie(ctx, user)
    page.goto(origin + "/cliente?aba=plano")
    assert "Gerar Pix Fast" in page.locator("main").inner_text()
    import payment_policies as policies, json

    rules = policies.rules(db, "renewal")
    for rule in rules:
        rule["priority"] = {"Pix AI": 1, "Pix Fast": 2, "Dinheiro": 3}[rule["method"]]
    db.execute(
        "UPDATE customer_payment_policies SET rules=? WHERE scope='renewal'",
        (json.dumps(rules),),
    )
    db.commit()
    page.reload()
    assert "Pagar com Pix AI" in page.locator("main").inner_text()
    assert "RENOVAR PLANO AGORA" not in page.locator("main").inner_text()
    assert not errors, errors
    browser.close()
server.shutdown()
print(
    "PASS: seven header widths/order/colors, mobile menu, masked code success/failure, preserved fields and saved AI-first renewal. Mocked email/API. Screenshots:",
    folder,
)
