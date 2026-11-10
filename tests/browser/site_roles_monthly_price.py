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

folder = Path(tempfile.mkdtemp(prefix="adelinha-roles-monthly-"))
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
from werkzeug.security import generate_password_hash
from flask import url_for

password = "fixture-interface-123"
for name, email, role, settings, users in [
    ("Admin fixture", "ui-admin@example.test", "administrador", 1, 1),
    ("Equipe fixture", "ui-crew@example.test", "atendente", 0, 0),
]:
    db.execute(
        "INSERT INTO staff_users(name,email,password_hash,role,can_settings,can_users,can_clients,can_packages,can_pickups,created_at) VALUES(?,?,?,?,?,?,1,1,1,?)",
        (
            name,
            email,
            generate_password_hash(password),
            role,
            settings,
            users,
            datetime.now(TZ).isoformat(),
        ),
    )
db.commit()
with app.test_request_context():
    actions_path = url_for("client_actions.admin_hub")
    plan_path = url_for("plans.admin_hub")
# A pending renewal must retain its price after the administrator changes the offer.
user = app.test_client()
with user.session_transaction() as s:
    s.update(client_id=1, client_session_version=1)
user.post(
    "/cliente/plano/assinar", data={"plan_rules": "yes", "csrf_token": token(user)}
)
pending = dict(db.execute("SELECT * FROM plan_orders WHERE state='pending'").fetchone())
server = make_server("127.0.0.1", 5091, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:5091"
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

    def audit():
        assert page.evaluate(
            "document.documentElement.scrollWidth<=innerWidth"
        ), page.url

    def login(email, staff_mode=False):
        page.goto(origin + ("/login" if staff_mode else "/cliente/login"))
        page.locator("[name=user]" if staff_mode else "[name=cpf]").fill(email)
        page.locator("[name=password]").fill(password if staff_mode else "senha-segura")
        page.get_by_role("button", name="Entrar", exact=True).click()
        page.wait_for_load_state("networkidle")

    def logout():
        if not page.locator("#main-nav").is_visible():
            page.locator("[data-nav-toggle]").click()
        page.locator("#main-nav").get_by_role("button", name="Sair", exact=True).click()
        page.wait_for_load_state("networkidle")

    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(origin + "/")
        page.wait_for_load_state("networkidle")
        audit()
        nav = page.locator("#main-nav")
        assert "Dúvidas" in nav.text_content() and "Blog" in nav.text_content()
        assert (
            nav.locator('a[href="/cliente/login"]').count() == 1
            and nav.locator('a[href="/login"]').count() == 1
        )
    login("cliente@example.com")
    for width in (320, 390, 768, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        for tab in (
            "visao-geral",
            "pacotes",
            "plano",
            "historico",
            "dados",
            "contratos",
        ):
            page.goto(origin + "/cliente?aba=" + tab)
            page.wait_for_load_state("networkidle")
            audit()
            nav = page.locator("#main-nav").text_content()
            assert (
                "Dúvidas" not in nav and "Gerenciar blog" not in nav and "Sair" in nav
            )
    logout()
    login("ui-crew@example.test", True)
    assert page.url.endswith("/painel")
    assert "Gerenciar blog" not in page.locator("#main-nav").text_content()
    assert ctx.request.get(origin + "/painel/blog").status == 403
    for width in (320, 390, 768, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(100)
        audit()
        assert (
            page.locator("#package-form").is_visible()
            and page.locator("#pickup-form").is_visible()
        )
    page.locator("#package-client-search").fill("CLIPCA-1")
    page.locator("#package-client-results button").first.click()
    for field, value in {
        "tracking": "UIRECEIPT3",
        "shelf": "A3",
        "width": "10",
        "height": "10",
        "length": "10",
        "weight": "1",
    }.items():
        page.locator(f"#package-form [name={field}]").fill(value)
    page.get_by_role("button", name="Registrar pacote", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert db.execute("SELECT 1 FROM packages WHERE tracking='UIRECEIPT3'").fetchone()
    page.locator('[name=package_ids][value="1"]').check()
    page.locator("[name=payment_method][value=Dinheiro]").check()
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
    signature = page.locator("[name=pickup_signature]").input_value()
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.locator("[name=pickup_signature]").input_value() == signature
    page.locator("[name=payment_confirmed]").check()
    page.get_by_role("button", name="Confirmar retirada", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert "Pedido retirado com sucesso!" in page.locator("main").inner_text()
    logout()
    login("ui-admin@example.test", True)
    assert "Gerenciar blog" in page.locator("#main-nav").text_content()
    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(origin + actions_path)
        page.wait_for_load_state("networkidle")
        audit()
        assert (
            page.locator("[name=title]").is_visible()
            and page.locator("[name=body]").is_visible()
        )
    page.locator("[name=title]").fill(
        "Atualização fictícia com título longo para conferir a largura dos cartões e das instruções"
    )
    page.locator("[name=explanation]").fill(
        "Confira esta instrução fictícia. Nenhuma autorização real será publicada."
    )
    page.locator("[name=body]").fill(
        "Conteúdo fictício, sem alteração de contratos. Apenas validação de leiaute e rascunho."
    )
    page.get_by_role(
        "button", name="Salvar rascunho e visualizar prévia", exact=True
    ).click()
    page.wait_for_load_state("networkidle")
    for width in (320, 390, 768, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(100)
        audit()
    cycle_before = dict(db.execute("SELECT * FROM plan_cycles").fetchone())
    page.goto(origin + plan_path)
    page.locator("[name=monthly_price]").fill("39,90")
    page.get_by_role("button", name="Salvar mensalidade", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert "Mensalidade atualizada com sucesso." in page.locator("main").inner_text()
    assert dict(db.execute("SELECT * FROM plan_cycles").fetchone()) == cycle_before
    assert (
        db.execute(
            "SELECT amount_cents FROM plan_orders WHERE id=?", (pending["id"],)
        ).fetchone()["amount_cents"]
        == 3390
    )
    logout()
    page.goto(origin + "/")
    assert "R$ 39,90" in page.locator(".monthly-price").inner_text()
    login("cliente@example.com")
    page.goto(origin + "/cliente?aba=plano")
    assert "R$ 39,90" in page.locator("main").inner_text()
    page.goto(origin + "/cliente/plano/pagamentos/" + pending["id"])
    assert "R$ 33,90" in page.locator("main").inner_text()
    assert fake.creates == 2
    browser.close()
server.shutdown()
db.close()
assert not errors, errors
print(
    json.dumps(
        {
            "widths": [320, 360, 390, 430, 768, 1024, 1440],
            "visitor_client_crew_admin": True,
            "receipt_and_pickup": True,
            "pending_long_text": True,
            "monthly_price_immutable_orders": True,
            "js_errors": errors,
            "artifacts": str(folder),
        }
    )
)
