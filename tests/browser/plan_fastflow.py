"""Local isolated Chromium simulation: never uses production DB or providers."""

import sys, tempfile, threading, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
# Install Playwright outside the checkout and supply it through PYTHONPATH.
from pathlib import Path
from datetime import datetime
from werkzeug.serving import make_server
from playwright.sync_api import sync_playwright
from app import create_app
from database import DatabaseConnection
from test_app import register
from test_plan import CONFIG, FakeFlow
import plan_routes, plan_service as ps
from core import TZ

folder = Path(tempfile.mkdtemp(prefix="adelinha-plan-browser-"))
path = str(folder / "fixture.db")
app = create_app(
    {
        **CONFIG,
        "DATABASE": path,
        "FASTFLOW_CARD_ENABLED": "1",
        "FASTFLOW_CARD_PCI_READY": "1",
    }
)
c = app.test_client()
register(c)
db = DatabaseConnection(path)
db.execute(
    "UPDATE clients SET confirmation_channel='email',confirmation_destination=email,confirmation_validated_at=accepted_at"
)
db.commit()
fake = FakeFlow()
plan_routes.gateway = lambda _: fake
server = make_server("127.0.0.1", 5058, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
results = []
errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"]
    )
    ctx = browser.new_context()
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    # Route is discovered from the authentication redirect, avoiding assumptions.
    page.goto("http://127.0.0.1:5058/")
    page.wait_for_load_state("networkidle")
    for width in (320,360,390,430,768,1024,1440):
        page.set_viewport_size({'width':width,'height':900})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        columns=page.locator('.monthly-offer').evaluate("e=>getComputedStyle(e).gridTemplateColumns.split(' ').length")
        assert columns==(1 if width<768 else 2)
    page.set_viewport_size({'width':1440,'height':1000})
    page.locator('.payment-options').screenshot(path=str(folder/'home-plan-1440.png'))
    page.set_viewport_size({'width':390,'height':844})
    page.locator('.payment-options').screenshot(path=str(folder/'home-plan-390.png'))
    page.get_by_role('link',name='Assinar plano',exact=True).click()
    page.wait_for_load_state('networkidle')
    page.locator("input[name=cpf]").fill("cliente@example.com")
    page.locator("input[name=password]").fill("senha-segura")
    page.get_by_role("button", name="Entrar", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert page.url.endswith("/cliente/plano")
    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto("http://127.0.0.1:5058/cliente/plano")
        page.wait_for_load_state("networkidle")
        assert page.locator("h1").inner_text() == "Plano ME GUARDE Mais"
        size = page.evaluate(
            "({w:innerWidth,scroll:document.documentElement.scrollWidth})"
        )
        assert size["scroll"] <= size["w"], size
        results.append({"width": width, "hub": True})
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.locator('[data-plan-method] option[value=card]').count()==0
    assert page.locator('[data-plan-card]').count()==0
    page.locator("input[name=plan_rules]").check()
    page.get_by_role("button", name="Assinar plano — R$ 33,90", exact=True).click()
    page.wait_for_load_state("networkidle")
    assert page.get_by_role("button", name="Copiar código Pix").is_visible()
    for width in (320, 390, 768, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        size = page.evaluate(
            "({w:innerWidth,scroll:document.documentElement.scrollWidth})"
        )
        assert size["scroll"] <= size["w"], size
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(folder / "pix-390.png"), full_page=True)
    mobile = browser.new_context(
        is_mobile=True, has_touch=True, viewport={"width": 390, "height": 844}
    )
    mobile.add_cookies(ctx.cookies())
    touch = mobile.new_page()
    touch.on("pageerror", lambda e: errors.append(str(e)))
    touch.goto(page.url)
    touch.wait_for_load_state("networkidle")
    assert touch.get_by_role("button", name="Copiar código Pix").is_visible()
    touch.get_by_role("button", name="Copiar código Pix").tap()
    assert touch.evaluate("document.documentElement.scrollWidth<=innerWidth")
    mobile.close()
    fake.records["1"]["status"] = "paid"
    # Emulates the server reconciliation used by webhook/jobs, without a real charge.
    order = dict(db.execute("SELECT * FROM plan_orders").fetchone())
    ps.apply_transaction(db, order["id"], fake.get("1"), datetime.now(TZ))
    page.reload()
    assert "Seu plano ME GUARDE Mais está ativo." in page.locator("main").inner_text()
    page.get_by_role("link", name="Acessar meu plano").click()
    page.wait_for_load_state("networkidle")
    assert "10" in page.locator(".plan-balance").inner_text()
    page.locator("input[name=plan_rules]").check()
    page.get_by_role("button", name="Renovar meu plano — R$ 33,90", exact=True).click()
    page.wait_for_load_state("networkidle")
    fake.records["2"]["status"] = "paid"
    order = dict(
        db.execute(
            "SELECT * FROM plan_orders WHERE transaction_id=?", ("2",)
        ).fetchone()
    )
    ps.apply_transaction(db, order["id"], fake.get("2"), datetime.now(TZ))
    page.reload()
    assert "garantida para o próximo ciclo" in page.locator("main").inner_text()
    assert "sem antecipação" in page.locator("main").inner_text()
    # No card field or sensitive payment values are saved to browser storage.
    assert page.evaluate("Object.keys(sessionStorage).every(k=>!k.includes('card'))")
    staff = app.test_client()
    with staff.session_transaction() as sess:
        sess.update(staff=True)
    cookie = staff.get_cookie("session")
    ctx.add_cookies(
        [{"name": "session", "value": cookie.value, "domain": "127.0.0.1", "path": "/"}]
    )
    for width in (320, 390, 768, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto("http://127.0.0.1:5058/painel/configuracoes/plano")
        page.wait_for_load_state("networkidle")
        assert page.locator("h1").inner_text() == "Plano ME GUARDE Mais"
        size = page.evaluate(
            "({w:innerWidth,scroll:document.documentElement.scrollWidth})"
        )
        assert size["scroll"] <= size["w"], size
    page.screenshot(path=str(folder / "admin.png"), full_page=True)
    browser.close()
server.shutdown()
db.close()
assert not errors, errors
print(
    json.dumps(
        {
            "widths": results,
            "pix": True,
            "activation": True,
            "early_renewal": True,
            "admin": True,
            "js_errors": errors,
            "artifacts": str(folder),
        },
        ensure_ascii=False,
    )
)
