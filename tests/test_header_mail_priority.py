"""Public navigation, registration validation and existing payment routing; no real mail/API."""

import json
from datetime import datetime, timedelta
import pytest
from test_plan import site, post_purchase, create_cycle
from test_customer_payments import payments, package, staff
from test_advance_payments import advance, customer
from test_app import token
from test_customization import csrf
from test_payment_policies_history import configure, form
import payment_policies as policies


def test_registration_invalid_fields_never_reach_gmail(payments, monkeypatch):
    app, client, _, _ = payments
    import app as module

    calls = []
    monkeypatch.setattr(
        module, "send_code", lambda *a: (calls.append(a) or (True, "accepted"))
    )
    for cpf, email, field in [
        ("11111111111", "valid@example.test", "cpf"),
        ("52998224725", "invalid", "email"),
    ]:
        response = client.post(
            "/cadastro/enviar-codigo",
            data={"cpf": cpf, "email": email, "csrf_token": token(client)},
        )
        assert response.status_code == 400 and response.json["field"] == field
        assert not calls


@pytest.mark.parametrize("accepted", [False, True])
def test_registration_message_only_after_gmail_acceptance(
    payments, monkeypatch, accepted
):
    app, client, _, _ = payments
    import app as module

    monkeypatch.setattr(
        module,
        "send_code",
        lambda *a: (
            accepted,
            (
                "Gmail API recusou a solicitação (403)"
                if not accepted
                else "accepted-message"
            ),
        ),
    )
    response = client.post(
        "/cadastro/enviar-codigo",
        data={
            "cpf": "529.982.247-25",
            "email": "vilmar@gmail.com",
            "csrf_token": token(client),
        },
    )
    if accepted:
        assert (
            response.json["message"]
            == "Enviamos um código para v***@g***.com. Confira sua caixa de entrada e, se necessário, a pasta de spam. Depois, digite o código abaixo."
        )
        with client.session_transaction() as s:
            assert s["registration_confirmation"]["email"] == "vilmar@gmail.com"
    else:
        assert response.status_code == 503 and "message" not in response.json
        assert "Não foi possível enviar" in response.json["error"]
        with client.session_transaction() as s:
            assert not s.get("registration_confirmation")


@pytest.mark.parametrize("scope", ["advance", "subscription", "renewal"])
def test_saved_ai_first_applies_to_new_charge_in_own_flow(payments, scope):
    app, client, db, fake = payments
    if scope == "renewal":
        create_cycle(db, fake, datetime.now().astimezone() - timedelta(days=5))
        fake.state = "pending"
    rules = policies.rules(db, scope)
    for rule in rules:
        rule["priority"] = {"Pix AI": 1, "Dinheiro": 2}[rule["method"]]
    staff(client)
    response = client.post(
        "/painel/configuracoes/pagamentos-cliente", data=form(client, rules, scope)
    )
    assert response.status_code == 302
    assert policies.rules(db, scope)[0]["priority"] == 1
    customer(client)
    before = fake.creates
    if scope == "advance":
        pid = package(db)
        review = client.post(
            "/cliente/pacotes/pagar",
            data={"package_ids": pid, "csrf_token": token(client)},
        )
        assert "Pagar com Pix AI" in review.text
        oid = advance(client, [pid]).json["id"]
        table = "pickup_payment_orders"
    else:
        page = client.get("/cliente/plano").text
        assert "Pagar com Pix AI" in page and ">Gerar Pix Fast<" not in page
        oid = post_purchase(client).location.rsplit("/", 1)[-1]
        table = "plan_orders"
    row = db.execute("SELECT * FROM " + table + " WHERE id=?", (oid,)).fetchone()
    assert row["local_method"] == "Pix AI" and row["state"] == "pending"
    assert fake.creates == before
    other = next(k for k in policies.SCOPES if k != scope)
    assert policies.rules(db, other)[0]["priority"] == 1








@pytest.mark.parametrize("outcome", ["accepted", "missing_id", "forbidden"])
def test_registration_through_actual_gmail_adapter_mocked_http(
    payments, monkeypatch, outcome
):
    import notifications
    from test_notifications import gmail_environment, Response
    from urllib.error import HTTPError
    from io import BytesIO

    gmail_environment(monkeypatch)
    calls = []

    def request(req, timeout):
        calls.append(req.full_url)
        if req.full_url.endswith("/token"):
            return Response({"access_token": "fixture-token"})
        if outcome == "forbidden":
            raise HTTPError(
                req.full_url,
                403,
                "Forbidden",
                {},
                BytesIO(b'{"error":{"code":403,"message":"Forbidden"}}'),
            )
        return Response({"id": "accepted-id"} if outcome == "accepted" else {})

    monkeypatch.setattr(notifications, "urlopen", request)
    _, client, _, _ = payments
    response = client.post(
        "/cadastro/enviar-codigo",
        data={
            "cpf": "52998224725",
            "email": "vilmar@gmail.com",
            "csrf_token": token(client),
        },
    )
    assert len(calls) == 2
    assert response.status_code == (200 if outcome == "accepted" else 503)
    assert ("message" in response.json) == (outcome == "accepted")
