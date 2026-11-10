"""Isolated Chromium checks for admin plan approval/cancellation. No real API/email."""

import sys, json, tempfile, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from datetime import datetime
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from app import create_app
from database import DatabaseConnection
from core import TZ
from test_app import register
from test_plan import CONFIG, FakeFlow
from fastflow_gateway import Settings
import plan_routes, plan_service as service

folder = Path(tempfile.mkdtemp(prefix="adelinha-admin-plan-browser-"))
app = create_app({**CONFIG, "DATABASE": str(folder / "fixture.db")})
web = app.test_client()
register(web)
db = DatabaseConnection(app.config["DATABASE"])
db.execute(
    "UPDATE clients SET confirmation_channel='email',confirmation_destination=email,confirmation_validated_at=accepted_at"
)
db.commit()
owner = db.execute("SELECT * FROM clients WHERE id=1").fetchone()
clone = dict(owner)
clone.pop("id")
clone.update(
    name="Cliente Manual Fictício",
    cpf="39053344705",
    public_id="CLIPCA-2",
    email="manual-fixture@example.test",
)
marks = ",".join("?" for _ in clone)
columns = ",".join(clone)
second = db.execute(
    f"INSERT INTO clients({columns}) VALUES({marks}) RETURNING id",
    tuple(clone.values()),
).fetchone()["id"]
db.commit()
fake = FakeFlow()
plan_routes.gateway = lambda _: fake
fake.cancel = lambda tid: fake.records[tid].update(status="cancelled")
now = datetime.now(TZ)
order = service.reserve_order(db, owner, "pix", now, Settings.from_config(CONFIG))[0]
data = fake.create(order, owner)
service.apply_transaction(db, order["id"], data, now)
with web.session_transaction() as session:
    session.update(staff=True)
server = make_server("127.0.0.1", 5059, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:5059"
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"]
    )
    context = browser.new_context()
    cookie = web.get_cookie("session")
    context.add_cookies(
        [{"name": "session", "value": cookie.value, "domain": "127.0.0.1", "path": "/"}]
    )
    page = context.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(origin + "/painel/configuracoes/plano")
    page.wait_for_load_state("networkidle")
    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(100)
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
    page.locator("input[name=q]").fill("CLIPCA-2")
    page.get_by_role("button", name="Buscar clientes", exact=True).click()
    page.wait_for_load_state("networkidle")
    page.get_by_role("link", name="Validar participação", exact=True).click()
    page.wait_for_load_state("networkidle")
    for width in (320, 390, 768, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(100)
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth"), width
    assert "Pix Fast depende do pagamento efetivo na API" in page.locator("main").inner_text()
    page.locator("textarea[name=reason]").fill(
        "Autorização administrativa conferida no teste fictício do ponto."
    )
    page.locator("input[name=payment_method][value=Dinheiro]").check()
    page.locator("input[name=payment_confirmed]").check()
    page.locator("input[name=confirm]").check()
    page.get_by_role("button", name="Confirmar pagamento e ativar plano", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert (
        "Plano ativado com sucesso"
        in page.locator("main").inner_text()
    )
    assert (
        db.execute(
            "SELECT COUNT(*) n FROM plan_cycles WHERE client_id=?", (second,)
        ).fetchone()["n"]
        == 1
    )
    assert fake.creates == 1
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(folder / "admin-390.png"), full_page=True)
    mobile = browser.new_context(
        is_mobile=True, has_touch=True, viewport={"width": 390, "height": 844}
    )
    mobile.add_cookies(context.cookies())
    touch = mobile.new_page()
    touch.on("pageerror", lambda e: errors.append(str(e)))
    touch.goto(
        origin + "/painel/configuracoes/plano/pedidos/" + order["id"] + "/cancel"
    )
    touch.wait_for_load_state("networkidle")
    assert touch.evaluate("document.documentElement.scrollWidth<=innerWidth")
    touch.locator("textarea[name=reason]").fill(
        "Solicitação não paga cancelada no teste fictício do ponto."
    )
    touch.locator("input[name=confirm]").tap()
    touch.screenshot(path=str(folder / "cancel-confirmation-390.png"), full_page=True)
    assert touch.locator("form").last.evaluate("e=>e.checkValidity()")
    with touch.expect_navigation(wait_until="networkidle"):
        touch.get_by_role("button", name="Confirmar cancelamento", exact=True).tap()
    assert "Solicitação não paga cancelada" in touch.locator("main").inner_text()
    assert (
        db.execute(
            "SELECT state FROM plan_orders WHERE id=?", (order["id"],)
        ).fetchone()["state"]
        == "admin_cancelled"
    )
    assert (
        db.execute("SELECT COUNT(*) n FROM plan_admin_decisions").fetchone()["n"] == 2
    )
    assert (
        db.execute("SELECT COUNT(*) n FROM plan_cycles WHERE client_id=1").fetchone()[
            "n"
        ]
        == 0
    )
    browser.close()
server.shutdown()
db.close()
assert not errors, errors
print(
    json.dumps(
        {
            "admin_widths": [320, 360, 390, 430, 768, 1024, 1440],
            "manual_approval": True,
            "unpaid_cancel_by_touch": True,
            "separate_audit": True,
            "js_errors": errors,
            "artifacts": str(folder),
        },
        ensure_ascii=False,
    )
)
