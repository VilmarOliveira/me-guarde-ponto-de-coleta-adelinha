"""Presentation keeps the existing policy contract and stable method field IDs."""

import re
import pytest
from test_plan import site
from test_customer_payments import payments
from test_customization import legacy_login
from test_payment_policies_history import form
from payment_policies import rules, select


@pytest.mark.parametrize("scope", ["advance", "subscription", "renewal"])
def test_new_order_survives_reload_and_is_independent(payments, scope):
    app, client, db, fake = payments
    legacy_login(client)
    items = rules(db, scope)
    # Cash becomes first; all limits/texts stay bound to the original method.
    items[0]["priority"] = 2
    items[1]["priority"] = 3
    items[2]["priority"] = 1
    items[0]["maximum"] = 50000
    items[1]["title"] = "Pix AI personalizado"
    response = client.post(
        "/painel/configuracoes/pagamentos-cliente",
        data=form(client, items, scope=scope),
        follow_redirects=True,
    )
    assert response.status_code == 200
    page = response.text
    rows = re.findall(r'<tr data-payment-row data-method="([^"]+)"', page)
    assert rows == ["Dinheiro", "Pix Fast", "Pix AI"]
    saved = rules(db, scope)
    assert [x["priority"] for x in saved] == [2, 3, 1]
    assert saved[0]["maximum"] == 50000 and saved[1]["title"] == "Pix AI personalizado"
    assert select(db, app, scope, 3390)["method"] == "Dinheiro"
    for other in ("advance", "subscription", "renewal"):
        if other != scope:
            assert [x["priority"] for x in rules(db, other)] == [1, 2, 3]
    assert fake.creates == 0
    assert (
        re.findall(
            r'<tr data-payment-row data-method="([^"]+)"',
            client.get("/painel/configuracoes/pagamentos-cliente?aba=" + scope).text,
        )
        == rows
    )
    assert all(f'name="priority_{i}"' in page for i in range(3))


def test_preview_reorders_without_saving_and_duplicate_order_rejected(payments):
    _, client, db, fake = payments
    legacy_login(client)
    items = rules(db, "advance")
    items[0]["priority"] = 2
    items[1]["priority"] = 1
    response = client.post(
        "/painel/configuracoes/pagamentos-cliente",
        data=form(client, items, action="preview"),
    )
    assert "Pagar com Pix AI" in response.text
    assert [x["priority"] for x in rules(db, "advance")] == [1, 2, 3]
    invalid = form(client, items)
    invalid["priority_0"] = "1"
    response = client.post("/painel/configuracoes/pagamentos-cliente", data=invalid)
    assert "Use prioridades diferentes" in response.text
    assert [x["priority"] for x in rules(db, "advance")] == [1, 2, 3]
    assert fake.creates == 0
