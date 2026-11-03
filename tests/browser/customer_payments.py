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

folder = Path(tempfile.mkdtemp(prefix="adelinha-four-updates-"))
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
owner = dict(db.execute("SELECT * FROM clients WHERE id=1").fetchone())
owner.pop("id")
owner.update(
    name="Cliente Fast Ficticio",
    email="fast-fixture@example.test",
    cpf="39053344705",
    public_id="CLIPCA-2",
)
cid = db.execute(
    "INSERT INTO clients("
    + ",".join(owner)
    + ") VALUES("
    + ",".join("?" for _ in owner)
    + ") RETURNING id",
    tuple(owner.values()),
).fetchone()["id"]
db.commit()
server = make_server("127.0.0.1", 5077, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:5077"
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
    user = app.test_client()
    with user.session_transaction() as s:
        s.update(client_id=1, client_session_version=1)
    cookie(ctx, user)
    for width in (320, 360, 390, 430, 768, 1024, 1440):
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
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), (
                width,
                tab,
            )
        if width < 768:
            assert not page.locator(".customer-area-menu").evaluate("e=>e.open")
            page.locator(".customer-area-menu summary").click()
            assert page.locator(".customer-tabs").is_visible()
    page.goto(origin + "/cliente?aba=pacotes")
    assert "Incluído no plano" in page.locator("main").inner_text()
    page.goto(origin + "/cliente?aba=plano")
    assert "9" in page.locator(".plan-balance").inner_text()
    assert page.locator("progress").get_attribute("value") == "1"
    with page.expect_download() as download:
        page.get_by_role("link", name="Baixar comprovante", exact=True).first.click()
    download.value.save_as(str(folder / "plan.pdf"))
    assert (folder / "plan.pdf").read_bytes().startswith(b"%PDF")
    cookie(ctx, web)
    page.goto(origin + "/painel")
    page.wait_for_load_state("networkidle")
    page.locator("#package-client-search").fill("CLIPCA-1")
    page.locator("#package-client-results button").first.click()
    for field in ("width", "height", "length"):
        page.locator(f"#package-form [name={field}]").fill("10")
    page.locator("#package-form [name=weight]").fill("1")
    page.wait_for_function(
        "document.querySelector('#package-plan-preview').textContent.includes('9 créditos disponíveis')"
    )
    assert "usará 1 crédito" in page.locator("#package-plan-preview").inner_text()
    page.locator("#package-form [name=weight]").fill("12")
    page.wait_for_function(
        "document.querySelector('#package-plan-preview').textContent.includes('usará 2 créditos')"
    )
    assert (
        db.execute("SELECT used_credits FROM plan_cycles").fetchone()["used_credits"]
        == 1
    )
    page.locator('[name=package_ids][value="1"]').check()
    page.locator('[name=payment_method][value="Pix AI"]').check()
    page.wait_for_timeout(400)
    assert page.locator("#pix-area").is_visible() and page.locator(
        "#pix-code"
    ).input_value().startswith("000201")
    page.locator('[name=payment_method][value="Dinheiro"]').check()
    page.wait_for_timeout(250)
    assert page.locator("#pix-area").is_hidden()
    fast_option = page.locator('[name=payment_method][value="Pix Fast"]')
    for value, allowed, total in [
        ("0.91", False, "R$ 3,99"),
        ("0.90", True, "R$ 4,00"),
    ]:
        page.locator("#discount-amount").fill(value)
        page.wait_for_function(
            "total=>document.querySelector('#pickup-total').textContent===total",
            arg=total,
        )
        assert fast_option.is_enabled() is allowed
        assert page.locator("#pickup-fast-range").is_visible() is (not allowed)
    page.locator("#discount-amount").fill("")
    page.locator("#surcharge-reason").fill("Teste fictício dos limites")
    for value, allowed, total in [
        ("995.10", True, "R$ 1000,00"),
        ("995.11", False, "R$ 1000,01"),
    ]:
        page.locator("#surcharge-amount").fill(value)
        page.wait_for_function(
            "total=>document.querySelector('#pickup-total').textContent===total",
            arg=total,
        )
        assert fast_option.is_enabled() is allowed
    assert "R$ 1.000,00" in page.locator("#pickup-fast-range").inner_text()
    assert "Avulso" in page.locator("#pickup-coverage").inner_text()
    page.locator("#surcharge-amount").fill("0")
    page.locator("#surcharge-reason").fill("")
    page.wait_for_function(
        "document.querySelector('#pickup-total').textContent==='R$ 4,90'"
    )
    assert fast_option.is_enabled()
    page.locator('[name=payment_method][value="Pix Fast"]').check()
    page.wait_for_timeout(250)
    page.get_by_role("button", name="Gerar Pix Fast", exact=True).click()
    page.wait_for_load_state("networkidle")
    page.wait_for_url("**/painel/retirada/pix-fast/*")
    item = db.execute("SELECT * FROM pickup_payment_orders").fetchone()
    assert (
        db.execute("SELECT status FROM packages WHERE id=1").fetchone()["status"]
        == "aguardando_retirada"
    )
    pay(ctx, item["id"], item["transaction_id"], "retiradas")
    page.reload()
    assert "Pagamento aprovado. Confirme a entrega" in page.locator("main").inner_text()
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator("[name=receiver_name]").fill("Pessoa Ficticia")
    page.locator("#document-type").select_option("CPF")
    page.locator("#document-number").fill("52998224725")
    canvas = page.locator("canvas.signature")
    canvas.scroll_into_view_if_needed()
    box = canvas.bounding_box()
    page.mouse.move(box["x"] + 20, box["y"] + 30)
    page.mouse.down()
    page.mouse.move(box["x"] + 100, box["y"] + 70, steps=8)
    page.mouse.up()
    before = page.locator("[name=pickup_signature]").input_value()
    page.set_viewport_size({"width": 768, "height": 900})
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.locator("[name=pickup_signature]").input_value() == before
    page.locator("[name=payment_confirmed]").check()
    page.screenshot(path=str(folder / "fast-delivery-390.png"), full_page=True)
    page.get_by_role("button", name="Confirmar retirada", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert "Pedido retirado com sucesso!" in page.locator("main").inner_text()
    assert (
        db.execute("SELECT used_credits FROM plan_cycles WHERE client_id=1").fetchone()[
            "used_credits"
        ]
        == 1
    )
    # Existing manual activation now explicitly records Dinheiro, not a free grant.
    page.goto(origin + "/painel/configuracoes/plano/clientes/1/validar")
    page.locator("[name=payment_method][value=Dinheiro]").check()
    page.locator("[name=payment_confirmed]").check()
    page.locator("[name=reason]").fill(
        "Recebimento em dinheiro conferido no teste ficticio."
    )
    page.locator("[name=confirm]").check()
    page.get_by_role(
        "button", name="Confirmar pagamento e ativar plano", exact=True
    ).click()
    page.wait_for_load_state("networkidle")
    assert "Renovação confirmada com sucesso" in page.locator("main").inner_text()
    page.goto(origin + f"/painel/configuracoes/plano/clientes/{cid}/validar")
    page.locator('[name=payment_method][value="Pix Fast"]').check()
    page.locator("[name=reason]").fill(
        "Cobrança FastFlow do plano em ambiente ficticio."
    )
    page.locator("[name=confirm]").check()
    page.get_by_role("button", name="Gerar Pix Fast", exact=True).click()
    page.wait_for_load_state("networkidle")
    page.wait_for_url("**/painel/configuracoes/plano/pagamentos/*")
    order = db.execute(
        "SELECT * FROM plan_orders WHERE client_id=? AND method='pix'", (cid,)
    ).fetchone()
    assert not db.execute(
        "SELECT 1 FROM plan_cycles WHERE client_id=?", (cid,)
    ).fetchone()
    pay(ctx, order["id"], order["transaction_id"], "pedidos")
    page.reload()
    assert "Plano ativado com sucesso" in page.locator("main").inner_text()
    cookie(ctx, user)
    page.goto(origin + "/cliente?aba=historico")
    page.wait_for_load_state("networkidle")
    assert "Pix Fast" in page.locator("main").inner_text()
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    page.screenshot(path=str(folder / "history-390.png"), full_page=True)
    browser.close()
server.shutdown()
db.close()
assert not errors, errors
print(
    json.dumps(
        {
            "tabs_widths": [320, 360, 390, 430, 768, 1024, 1440],
            "three_methods": True,
            "coverage_preview": True,
            "pickup_fast_boundaries": [3.99, 4, 1000, 1000.01],
            "separate_physical_delivery": True,
            "signature_resize": True,
            "manual_renewal": True,
            "fast_plan": True,
            "pdf": True,
            "js_errors": errors,
            "artifacts": str(folder),
        },
        ensure_ascii=False,
    )
)
