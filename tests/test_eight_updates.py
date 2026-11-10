"""Requested client account, Pix retry and public loading behavior; mocked APIs."""

from datetime import datetime, timedelta
from io import BytesIO
import pytest
from PIL import Image
from core import TZ
from test_app import token
from test_plan import site, post_purchase, create_cycle
from test_customer_payments import payments
from test_advance_payments import customer


def test_home_login_labels_and_deferred_banners(payments):
    app, client, db, _ = payments
    home = app.test_client().get("/").text
    assert (
        "Cadastre-se em nosso site" in home
        and "Antes de fazer sua compra, crie sua conta" not in home
    )
    assert (
        "> LOGIN</a>" in home
        and 'href="/cliente/login" data-metric="area_cliente"' in home
    )
    customer(client)
    assert 'href="/cliente" data-metric="area_cliente"' in client.get("/").text
    page = client.get("/cliente?aba=plano").text
    assert "ME GUARDE Mais" not in page and "Pagar com Pix AI" in page
    assert 'fetchpriority="high"' in home and "largura=640" in home


def test_unlink_requires_password_and_preserves_records(payments):
    _, client, db, _ = payments
    db.execute(
        "INSERT INTO google_client_identities VALUES('fixture-sub',1,'cliente@example.com',1)"
    )
    db.commit()
    customer(client)
    before = dict(db.execute("SELECT * FROM clients").fetchone())
    assert "Desvincular conta Google" in client.get("/cliente?aba=dados").text
    path = "/cliente/google/desvincular"
    assert client.get(path).status_code == 200
    for fields in (
        {"password": "wrong", "confirm": "yes"},
        {"password": "senha-segura"},
    ):
        assert (
            client.post(path, data={**fields, "csrf_token": token(client)}).status_code
            == 400
        )
        assert db.execute("SELECT 1 FROM google_client_identities").fetchone()
    assert (
        client.post(
            path,
            data={
                "password": "senha-segura",
                "confirm": "yes",
                "csrf_token": token(client),
            },
        ).status_code
        == 302
    )
    assert not db.execute("SELECT 1 FROM google_client_identities").fetchone()
    after = dict(db.execute("SELECT * FROM clients").fetchone())
    assert {k: v for k, v in after.items() if k != "session_version"} == {
        k: v for k, v in before.items() if k != "session_version"
    }
    assert after["session_version"] == before["session_version"] + 1
    assert client.get("/cliente").status_code == 200


def test_google_only_cannot_unlink(payments):
    _, client, db, _ = payments
    db.execute(
        "INSERT INTO google_client_identities VALUES('fixture-sub',1,'cliente@example.com',1)"
    )
    db.execute("UPDATE clients SET password_hash=NULL")
    db.commit()
    customer(client)
    result = client.post(
        "/cliente/google/desvincular",
        data={"confirm": "yes", "csrf_token": token(client)},
        follow_redirects=True,
    )
    assert "Crie uma senha primeiro" in result.text
    assert db.execute("SELECT 1 FROM google_client_identities").fetchone()
    assert "Criar senha por e-mail" in result.text






def test_banner_derivative_cache_original_and_private_pages(payments):
    app, client, db, _ = payments
    image = Image.new("RGB", (2048, 684), "orange")
    out = BytesIO()
    image.save(out, "PNG")
    data = out.getvalue()
    db.execute(
        "UPDATE settings SET main_banner_image=?,main_banner_mime='image/png',main_banner_updated_at='new-revision'",
        (data,),
    )
    db.commit()
    guest = app.test_client()
    assert guest.get("/identidade/banner-principal").data == data
    result = guest.get("/identidade/banner-principal?largura=640&v=new-revision")
    assert Image.open(BytesIO(result.data)).size == (640, 214)
    assert (
        result.mimetype == "image/webp" and "public" in result.headers["Cache-Control"]
    )
    assert (
        guest.get(
            "/identidade/banner-principal?largura=640&v=new-revision",
            headers={"If-None-Match": result.headers["ETag"]},
        ).status_code
        == 304
    )
    customer(client)
    assert "no-store" in client.get("/cliente?aba=dados").headers["Cache-Control"]
    assert guest.get("/identidade/banner-principal?largura=9000").status_code == 400
