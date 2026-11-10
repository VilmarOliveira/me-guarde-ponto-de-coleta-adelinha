"""Four updates: owner-scoped tabs and isolated, mocked payment purposes."""

import json, hmac, hashlib, uuid, re
from datetime import datetime, timedelta
import pytest
import requests
from test_plan import site, CONFIG, post_purchase
from test_app import token, signature
from core import TZ
import plan_service as ps
import pickup_payments as pp


@pytest.fixture
def payments(site, monkeypatch):
    app, client, db, fake = site
    db.execute(
        "UPDATE settings SET pix_receiver_name='Responsavel Teste',pix_key_type='CNPJ',pix_key='38145273000105'"
    )
    db.commit()
    return site


def staff(client):
    with client.session_transaction() as s:
        s.clear()
        s.update(staff=True)


def package(db, number=1, base=4.9, client_id=1):
    now = datetime.now(TZ).isoformat()
    result = db.execute(
        "INSERT INTO packages(client_id,public_id,tracking,width,height,length,weight,shelf,category,base_price,status,created_at,notified_at) VALUES(?,?,?,10,10,10,1,'A1','Pequeno',?,'aguardando_retirada',?,?) RETURNING id",
        (client_id, f"PCTPCA-{number}", f"PKG{number}", base, now, now),
    ).fetchone()["id"]
    db.commit()
    return result


def create_pickup(client, ids, **extra):
    return client.post(
        "/painel/retirada/cobrancas",
        data={
            "package_ids": [str(i) for i in ids],
            "csrf_token": token(client),
            **extra,
        },
    )


def deliver(client, ids, method="Dinheiro", **extra):
    return client.post(
        "/retiradas",
        data={
            "package_ids": [str(i) for i in ids],
            "receiver_name": "Pessoa Ficticia",
            "receiver_document": "52998224725",
            "document_type": "CPF",
            "pickup_signature": signature(),
            "payment_method": method,
            "payment_confirmed": "yes",
            "csrf_token": token(client),
            **extra,
        },
    )




def admin_form(client, path, method):
    response = client.get(path)
    assert response.status_code == 200
    data = {
        n: re.search('name="' + n + '" value="([^"]*)"', response.text).group(1)
        for n in ("operation_id", "cycle_key")
    }
    return {
        **data,
        "csrf_token": token(client),
        "reason": "Conferencia de pagamento ficticio no ponto.",
        "confirm": "yes",
        "payment_confirmed": "yes",
        "payment_method": method,
    }


def test_six_tabs_owned_counts_and_password_rotation(payments):
    app, client, db, _ = payments
    package(db)
    for tab in ("visao-geral", "pacotes", "plano", "historico", "dados", "contratos"):
        page = client.get("/cliente?aba=" + tab)
        assert page.status_code == 200 and 'class="customer-tabs"' in page.text
    assert client.get("/cliente?aba=invalid").status_code == 404
    other = app.test_client()
    with client.session_transaction() as s:
        with other.session_transaction() as old:
            old.update(dict(s))
    response = client.post(
        "/cliente/alterar-senha",
        data={
            "current_password": "senha-segura",
            "password": "nova-senha-123",
            "password_confirm": "nova-senha-123",
            "csrf_token": token(client),
        },
    )
    assert (
        response.status_code == 302
        and client.get("/cliente?aba=dados").status_code == 200
    )
    assert other.get("/cliente").status_code == 302


@pytest.mark.parametrize("method", ["Pix AI", "Dinheiro"])
def test_manual_pickup_does_not_call_api_and_records_names(
    payments, monkeypatch, method
):
    _, client, db, fake = payments
    pid = package(db)
    staff(client)
    monkeypatch.setattr(
        "requests.sessions.Session.request", lambda *a, **k: pytest.fail("No API for manual payment")
    )
    response = deliver(client, [pid], method)
    assert response.status_code == 302
    row = db.execute("SELECT * FROM pickups").fetchone()
    assert row["payment_method"] == method and row["payment_status"] == "paid_manual"
    assert db.execute("SELECT status FROM packages").fetchone()["status"] == "retirado"














@pytest.mark.parametrize("method", ["Pix AI", "Dinheiro"])
def test_manual_plan_exact_methods_receipt_and_no_api(payments, monkeypatch, method):
    _, client, db, fake = payments
    staff(client)
    path = "/painel/configuracoes/plano/clientes/1/validar"
    form = admin_form(client, path, method)
    monkeypatch.setattr(
        "requests.sessions.Session.request", lambda *a, **k: pytest.fail("No API for manual plan")
    )
    response = client.post(path, data=form, follow_redirects=True)
    assert response.status_code == 200 and "Plano ativado com sucesso" in response.text
    row = db.execute("SELECT * FROM plan_orders").fetchone()
    assert (
        row["local_method"] == method
        and row["manual_paid_at"]
        and row["paid_at"] is None
        and row["accepted_at"] == ""
    )
    assert (
        db.execute("SELECT payment_method FROM plan_admin_decisions").fetchone()[
            "payment_method"
        ]
        == method
    )
    assert client.get(
        "/painel/configuracoes/plano/pagamentos/" + row["id"] + "/comprovante.pdf"
    ).data.startswith(b"%PDF")












def test_owner_scope_on_new_tabs_and_receipts(payments):
    app, client, db, fake = payments
    post_purchase(client)
    oid = db.execute("SELECT id FROM plan_orders").fetchone()["id"]
    clone = dict(db.execute("SELECT * FROM clients WHERE id=1").fetchone())
    clone.pop("id")
    clone.update(
        name="Outro Cliente Ficticio",
        cpf="39053344705",
        email="outro@example.test",
        public_id="CLIPCA-2",
    )
    cid = db.execute(
        "INSERT INTO clients("
        + ",".join(clone)
        + ") VALUES("
        + ",".join("?" for _ in clone)
        + ") RETURNING id",
        tuple(clone.values()),
    ).fetchone()["id"]
    db.commit()
    package(db, number=9, client_id=cid)
    assert "PCTPCA-9" not in client.get("/cliente?aba=pacotes").text
    with client.session_transaction() as s:
        s.clear()
        s.update(client_id=cid, client_session_version=1)
    assert (
        client.get("/cliente/plano/pagamentos/" + oid + "/comprovante.pdf").status_code
        == 404
    )
    assert client.get("/painel/retirada/cobrancas/nonexistent").status_code == 302














def preview(client, **overrides):
    return client.post(
        "/painel/pacotes/cobertura",
        data={
            "client_id": 1,
            "width": "10",
            "height": "10",
            "length": "10",
            "weight": "1",
            "csrf_token": token(client),
            **overrides,
        },
    )


def test_preview_readonly_uses_category_and_balance(payments):
    _, client, db, fake = payments
    from test_plan import create_cycle
    create_cycle(db,value=datetime.now(TZ))
    db.execute("UPDATE plan_cycles SET used_credits=4")
    db.commit()
    staff(client)
    before = db.execute("SELECT COUNT(*) n FROM plan_credit_ledger").fetchone()["n"]
    small = preview(client).json
    assert (
        small["message"]
        == "Plano ativo · 6 créditos disponíveis · Este pacote usará 1 crédito."
    )
    large = preview(client, width="40", height="40", length="40", weight="12").json
    assert large["category"] == "Grande" and large["credits"] == 2
    assert (
        db.execute("SELECT used_credits FROM plan_cycles").fetchone()["used_credits"]
        == 4
    )
    assert (
        db.execute("SELECT COUNT(*) n FROM plan_credit_ledger").fetchone()["n"]
        == before
    )
    db.execute("UPDATE plan_cycles SET used_credits=9")
    db.commit()
    insufficient = preview(
        client, width="40", height="40", length="40", weight="12"
    ).json
    assert (
        insufficient["message"] == "Recebimento avulso · R$ 9,90."
        and not insufficient["covered"]
    )
    assert preview(client, width="NaN").status_code == 400
    assert preview(client, client_id=999).status_code == 400


def test_receipt_coverage_persists_after_expiry_and_not_retroactive(payments):
    _, client, db, fake = payments
    old = package(db)
    staff(client)
    assert preview(client).json["message"] == "Recebimento avulso · R$ 4,90."
    with client.session_transaction() as session:
        session.clear()
        session.update(client_id=1, client_session_version=1)
    from test_plan import create_cycle
    create_cycle(db,value=datetime.now(TZ))
    staff(client)
    response = client.post(
        "/pacotes",
        data={
            "client_id": 1,
            "tracking": "COVERNEW",
            "shelf": "A2",
            "width": 10,
            "height": 10,
            "length": 10,
            "weight": 1,
            "csrf_token": token(client),
        },
    )
    assert response.status_code == 302
    covered = db.execute("SELECT * FROM packages WHERE tracking='COVERNEW'").fetchone()
    assert covered["base_price"] == 0 and ps.coverage(db, covered["id"])["amount"] == -1
    assert ps.coverage(db, old) is None
    before = db.execute(
        "SELECT COUNT(*) n FROM plan_credit_ledger WHERE kind='consume'"
    ).fetchone()["n"]
    db.execute(
        "UPDATE plan_cycles SET ends_at=?",
        (ps.iso(datetime.now(TZ) - timedelta(days=1)),),
    )
    db.execute(
        "UPDATE packages SET status='aguardando_retirada',notified_at=?",
        (ps.iso(datetime.now(TZ) - timedelta(days=5)),),
    )
    db.commit()
    assert not preview(client).json["covered"]
    summary = client.post(
        "/painel/retirada/resumo",
        data={"package_ids": [old, covered["id"]], "csrf_token": token(client)},
    ).json
    messages = {p["id"]: p for p in summary["package_coverage"]}
    assert (
        messages[covered["id"]]["message"]
        == "Incluído no plano · 1 crédito já utilizado · Recebimento: R$ 0,00."
    )
    assert messages[old]["message"] == "Avulso · Recebimento: R$ 4,90."
    assert messages[old]["late"] > 0 and messages[covered["id"]]["late"] > 0
    assert "Incluído no plano" in client.get("/painel").text
    assert deliver(client, [old, covered["id"]], "Dinheiro").status_code == 302
    assert (
        db.execute(
            "SELECT COUNT(*) n FROM plan_credit_ledger WHERE kind='consume'"
        ).fetchone()["n"]
        == before
    )
    assert ps.coverage(db, covered["id"]) is not None and ps.coverage(db, old) is None


def test_coverage_preview_requires_staff(payments):
    _, client, _, _ = payments
    assert preview(client).status_code == 302
