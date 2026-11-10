"""Responsive manual-only workflows; no remote provider or real billing."""

import sys, tempfile, threading, json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from app import create_app
from database import DatabaseConnection
from test_app import register, token
from test_customer_payments import package
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server
import payment_policies as policies

folder = Path(tempfile.mkdtemp(prefix="adelinha-local-payments-"))
app = create_app(
    {
        "TESTING": True,
        "SECRET_KEY": "manual-browser",
        "DATABASE": str(folder / "fixture.db"),
    }
)
user = app.test_client()
register(user)
db = DatabaseConnection(app.config["DATABASE"])
db.execute(
    "UPDATE clients SET confirmation_channel='email',confirmation_destination=email,confirmation_validated_at=accepted_at"
)
db.execute(
    "UPDATE settings SET pix_receiver_name='Fixture',pix_key_type='CNPJ',pix_key='38145273000105'"
)
db.commit()
pids = [package(db, number=i) for i in (1, 2)]
with user.session_transaction() as s:
    s.update(client_id=1, client_session_version=1)
staff = app.test_client()
with staff.session_transaction() as s:
    s.update(staff=True, staff_legacy=True)
server = make_server("127.0.0.1", 5103, app)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:5103"


def cookie(ctx, client):
    page.wait_for_load_state("networkidle")
    ctx.clear_cookies()
    ctx.add_cookies(
        [
            {
                "name": "session",
                "value": client.get_cookie("session").value,
                "domain": "127.0.0.1",
                "path": "/",
            }
        ]
    )


with sync_playwright() as p:
    browser = p.chromium.launch(
        executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"]
    )
    ctx = browser.new_context()
    page = ctx.new_page()
    errors = []
    requests = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: requests.append(r.url))
    for width in (320, 360, 390, 430, 768, 1024, 1440):
        page.set_viewport_size({"width": width, "height": 900})
        for client, paths in [
            (user, ["/cliente?aba=plano", "/cliente?aba=pacotes"]),
            (
                staff,
                [
                    "/painel",
                    "/painel/configuracoes/plano",
                    "/painel/configuracoes/pagamentos-cliente",
                    "/painel/configuracoes/plano/clientes/1/validar",
                ],
            ),
        ]:
            cookie(ctx, client)
            for path in paths:
                response = page.goto(origin + path)
                assert response.status == 200, (path, response.status)
                assert "Pix Fast" not in page.locator("main").inner_text()
                assert page.evaluate(
                    "document.documentElement.scrollWidth<=innerWidth"
                ), (width, path)
        page.screenshot(
            path=str(folder / (str(width) + "-manual-plan.png")), full_page=True
        )
    page.set_viewport_size(
        {"width": 390 if os.getenv("RUN_SMALL_SCREEN") else 1440, "height": 900}
    )
    for renewal in (False, True):
        cookie(ctx, user)
        page.goto(origin + "/cliente?aba=plano")
        page.locator("[name=plan_rules]").check()
        page.get_by_role("button", name="Pagar com Pix AI", exact=False).click()
        oid = page.url.rsplit("/", 1)[-1]
        assert page.locator("#plan-pix").is_visible()
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        assert "Nenhum crédito foi liberado" in page.locator("main").inner_text()
        cookie(ctx, staff)
        page.goto(origin + "/painel/configuracoes/plano/pagamentos/" + oid)
        page.get_by_role("link", name="Conferir e confirmar pagamento").click()
        page.locator("[name=reason]").fill(
            "Conferência fictícia de pagamento no ponto."
        )
        page.locator("[name=payment_confirmed]").check()
        page.locator("[name=confirm]").check()
        page.get_by_role("button", name="Confirmar pagamento e ativar plano").click()
        assert db.execute("SELECT COUNT(*) n FROM plan_cycles").fetchone()[
            "n"
        ] == 1 + int(renewal)
    assert db.execute("SELECT COUNT(*) n FROM plan_credit_ledger").fetchone()["n"] == 1
    for method, pid in zip(["Pix AI", "Dinheiro"], pids):
        rules = policies.rules(db, "advance")
        for rule in rules:
            rule["priority"] = 1 if rule["method"] == method else 2
        db.execute(
            "UPDATE customer_payment_policies SET rules=? WHERE scope='advance'",
            (json.dumps(rules),),
        )
        db.commit()
        cookie(ctx, user)
        page.goto(origin + "/cliente?aba=pacotes")
        page.locator(f'[name=package_ids][value="{pid}"]').check()
        page.get_by_role("button", name="Pagar antes da retirada", exact=True).click()
        page.get_by_role(
            "button",
            name=(
                "Pagar com Pix AI"
                if method == "Pix AI"
                else "Pagar em dinheiro no ponto"
            ),
            exact=True,
        ).click()
        page.wait_for_url("**/cliente/pacotes/pagamentos/*")
        oid = page.url.rsplit("/", 1)[-1]
        cookie(ctx, staff)
        page.goto(origin + "/painel/retirada/cobrancas/" + oid)
        page.locator("[name=payment_confirmed]").check()
        page.get_by_role(
            "button", name="Confirmar pagamento recebido", exact=True
        ).click()
        page.goto(origin + "/painel")
        page.locator(f'[name=package_ids][value="{pid}"]').check()
        form = page.locator("#pickup-form")
        form.locator("[name=receiver_name]").fill("Pessoa Fictícia")
        form.locator("[name=document_type]").select_option("CPF")
        form.locator("[name=receiver_document]").fill("52998224725")
        form.locator("[name=payment_method][value=Dinheiro]").check()
        page.wait_for_function(
            "document.querySelector('#pickup-total').textContent==='R$ 0,00'"
        )
        canvas = form.locator("canvas")
        canvas.scroll_into_view_if_needed()
        box = canvas.bounding_box()
        page.mouse.move(box["x"] + 15, box["y"] + 25)
        page.mouse.down()
        page.mouse.move(box["x"] + 70, box["y"] + 55, steps=8)
        page.mouse.up()
        form.locator("[name=payment_confirmed]").check()
        form.locator("#pickup-submit").click()
        try:
            page.wait_for_url("**/painel/pacotes/*?retirada=sucesso", timeout=5000)
        except Exception:
            print(
                "PICKUP FAILED",
                method,
                page.url,
                page.locator(".flash").all_inner_texts(),
                errors,
                flush=True,
            )
            raise
    assert db.execute("SELECT COUNT(*) n FROM pickups").fetchone()["n"] == 2
    assert db.execute("SELECT SUM(total_paid) n FROM pickups").fetchone()["n"] == 0
    assert not [
        u for u in requests if "fastflow" in u or "fastdepix" in u or "/pix-fast" in u
    ]
    assert not errors, errors
    browser.close()
server.shutdown()
print(
    "PASS: seven widths; subscription/renewal, Pix AI/cash advance, zero-balance physical pickups; no Fast requests. Screenshots:",
    folder,
)
