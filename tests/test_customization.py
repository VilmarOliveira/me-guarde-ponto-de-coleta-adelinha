import io
import json
import sqlite3

from PIL import Image
from werkzeug.security import generate_password_hash

from app import create_app, format_blog_body


def csrf(client):
    with client.session_transaction() as session:
        session.setdefault("csrf_token", "customization-csrf")
        return session["csrf_token"]


def legacy_login(client):
    with client.session_transaction() as session:
        session["staff"] = "staff"
        session["staff_legacy"] = True
        session["csrf_token"] = "customization-csrf"


def test_me_guarde_home_support_and_email_only_recovery(tmp_path):
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": str(tmp_path / "app.db")})
    client = app.test_client()
    home = client.get("/").text
    hero = home.split('<section class="main-service-banner"', 1)[1].split("</section>", 1)[0]
    assert "ME GUARDE" in hero and "COMPROU? DEIXA COM A GENTE." in hero
    assert "PONTO DE COLETA ADELINHA" not in hero.upper()
    assert "Recebemos e guardamos suas compras" not in hero
    assert 'aria-label="Fale com a gente pelo WhatsApp"' in home
    assert "wa.me/5516988004966" in home
    recovery = client.get("/cliente/recuperar-senha").text
    assert "exclusivamente ao e-mail" in recovery
    assert 'name="channel"' not in recovery
    rejected = client.post("/cliente/recuperar-senha", data={"stage": "request", "cpf": "52998224725", "channel": "whatsapp", "csrf_token": csrf(client)})
    assert rejected.status_code == 400


def test_banner_upload_home_display_and_persistence(tmp_path):
    database = str(tmp_path / "app.db")
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": database})
    client = app.test_client(); legacy_login(client)
    output = io.BytesIO()
    Image.new("RGB", (24, 24), "orange").save(output, "WEBP")
    image = output.getvalue()
    response = client.post("/painel/configuracoes/banners", data={
        "position": "1", "active": "yes", "alt_text": "Cliente recebendo uma encomenda",
        "image": (io.BytesIO(image), "banner.webp"), "csrf_token": csrf(client),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200 and "Banner salvo" in response.text
    home = client.get("/").text
    assert "Cliente recebendo uma encomenda" in home and "data-carousel" in home
    assert client.get("/banners/1/imagem").data == image
    restarted = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": database}).test_client()
    assert restarted.get("/banners/1/imagem").data == image


def test_legacy_main_banner_upload_persists_in_unified_carousel(tmp_path):
    database = str(tmp_path / "app.db")
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": database})
    client = app.test_client(); legacy_login(client)
    output = io.BytesIO()
    Image.new("RGB", (960, 300), "white").save(output, "WEBP")
    image = output.getvalue()
    response = client.post("/painel/configuracoes/identidade", data={
        "action": "main_banner", "main_banner_image": (io.BytesIO(image), "principal.webp"),
        "csrf_token": csrf(client),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert "Banner principal Me Guarde" in response.text
    assert client.get("/identidade/banner-principal").data == image
    restarted = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": database}).test_client()
    assert restarted.get("/identidade/banner-principal").data == image
    home = restarted.get("/").text
    assert 'class="main-service-banner"' in home
    assert 'class="hero-service"' not in home


def test_theme_persists_and_staff_permissions_are_enforced(tmp_path):
    database = str(tmp_path / "app.db")
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": database})
    client = app.test_client(); legacy_login(client)
    colors = {"primary":"#cc3300","secondary":"#442277","page":"#ffffff","surface":"#ffffff",
              "text":"#111111","link":"#442277","border":"#cccccc","header":"#ffffff",
              "footer":"#111111","button":"#cc3300","button_hover":"#992200"}
    result = client.post("/painel/configuracoes/aparencia", data={"name":"Campanha", **colors, "csrf_token":csrf(client)}, follow_redirects=True)
    assert "Tema salvo e ativado" in result.text
    assert "--orange:#cc3300" in client.get("/").text
    created = client.post("/painel/configuracoes/usuarios", data={
        "name":"Administrador Principal", "email":"principal@example.com", "password":"senha-segura",
        "role":"administrador", "is_primary":"yes", "password_confirm":"senha-segura", "csrf_token":csrf(client)}, follow_redirects=True)
    assert "Usuário criado" in created.text
    client.post("/painel/configuracoes/usuarios", data={
        "name":"Atendente Limitado", "email":"atendente@example.com", "password":"senha-segura",
        "role":"atendente", "can_clients":"yes", "password_confirm":"senha-segura", "csrf_token":csrf(client)})
    client.post("/sair", data={"csrf_token": csrf(client)})
    client.post("/login", data={"user":"atendente@example.com", "password":"senha-segura", "csrf_token":csrf(client)})
    assert client.get("/painel/configuracoes/aparencia").status_code == 403
    assert client.post("/painel/retirada/resumo", data={"package_ids":"1", "csrf_token":csrf(client)}).status_code == 403
    restarted = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": database}).test_client()
    assert "--orange:#cc3300" in restarted.get("/").text


def test_contract_confirmation_is_email_only_and_bound_to_email(tmp_path, monkeypatch):
    import app as app_module
    sent = []
    monkeypatch.setattr(app_module, "send_code", lambda channel, destination, code: (sent.append((channel, destination, code)) or (True, "gmail-id")))
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": str(tmp_path / "app.db")})
    client = app.test_client()
    page = client.get("/cadastro").text
    assert "Enviaremos um código para o e-mail informado no cadastro." in page
    assert "Enviar código por e-mail" in page
    assert 'name="confirmation_channel"' not in page
    rejected = client.post("/cadastro/enviar-codigo", data={"cpf":"52998224725","email":"cliente@example.com","confirmation_channel":"whatsapp","csrf_token":csrf(client)})
    assert rejected.status_code == 400 and not sent
    accepted = client.post("/cadastro/enviar-codigo", data={"cpf":"52998224725","email":"cliente@example.com","csrf_token":csrf(client)})
    assert accepted.status_code == 200 and sent[0][:2] == ("email","cliente@example.com")
    with client.session_transaction() as session:
        assert session["registration_confirmation"]["email"] == "cliente@example.com"
        assert session["registration_confirmation"]["channel"] == "email"


def test_theme_edit_delete_and_photo_persistence(tmp_path):
    database = str(tmp_path / "app.db")
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": database})
    client = app.test_client(); legacy_login(client)
    base = {"primary":"#cc3300","secondary":"#442277","page":"#ffffff","surface":"#ffffff","text":"#111111","link":"#442277","border":"#cccccc","header":"#ffffff","footer":"#111111","button":"#cc3300","button_hover":"#992200"}
    client.post("/painel/configuracoes/aparencia",data={"name":"Campanha",**base,"csrf_token":csrf(client)})
    changed={**base,"primary":"#006699"}
    edited=client.post("/painel/configuracoes/aparencia",data={"theme_id":"1","name":"Campanha atualizada",**changed,"csrf_token":csrf(client)},follow_redirects=True)
    assert "Alterações do tema salvas" in edited.text
    db=sqlite3.connect(database)
    assert db.execute("SELECT COUNT(*),name FROM site_themes").fetchone()==(1,"Campanha atualizada")
    output=io.BytesIO(); Image.new("RGB",(32,24),"blue").save(output,"JPEG"); photo=output.getvalue()
    uploaded=client.post("/painel/configuracoes/fotos",data={"kind":"Fachada","position":"1","alt_text":"Fachada real do ponto","image":(io.BytesIO(photo),"fachada.jpg"),"csrf_token":csrf(client)},content_type="multipart/form-data",follow_redirects=True)
    assert "Foto salva" in uploaded.text and "Fachada real do ponto" in client.get("/").text
    deleted=client.post("/painel/configuracoes/aparencia",data={"action":"delete","theme_id":"1","csrf_token":csrf(client)},follow_redirects=True)
    assert "cores padrão foram restauradas" in deleted.text
    assert db.execute("SELECT active_theme_id FROM settings WHERE id=1").fetchone()[0] is None
    restarted=create_app({"TESTING":True,"SECRET_KEY":"test","DATABASE":database}).test_client()
    assert restarted.get("/fotos/1/imagem").data == photo


def test_manager_access_and_deactivated_session_is_revoked(tmp_path):
    database=str(tmp_path/"app.db"); app=create_app({"TESTING":True,"SECRET_KEY":"test","DATABASE":database}); client=app.test_client(); legacy_login(client)
    client.post("/painel/configuracoes/usuarios",data={"name":"Gerente Operacional","email":"gerente@example.com","password":"senha-segura","password_confirm":"senha-segura","role":"gerente","can_clients":"yes","can_packages":"yes","can_pickups":"yes","csrf_token":csrf(client)})
    client.post("/sair",data={"csrf_token":csrf(client)}); client.post("/login",data={"user":"gerente@example.com","password":"senha-segura","csrf_token":csrf(client)})
    assert client.get("/painel").status_code==200
    assert client.get("/painel/configuracoes").status_code==200
    assert client.get("/painel/configuracoes/pagamentos-cliente").status_code==403
    db=sqlite3.connect(database); db.execute("UPDATE staff_users SET active=0,session_version=session_version+1 WHERE email='gerente@example.com'"); db.commit()
    response=client.get("/painel")
    assert response.status_code==302 and "/login" in response.headers["Location"]


def test_settings_hub_and_safe_legacy_admin_replacement(tmp_path):
    database = str(tmp_path / "app.db")
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": database})
    client = app.test_client(); legacy_login(client)
    dashboard = client.get("/painel").text
    assert ">Banners<" not in dashboard and ">Aparência<" not in dashboard
    hub = client.get("/painel/configuracoes").text
    assert "Identidade visual" in hub and "Usuários e permissões" in hub
    client.post("/painel/configuracoes/usuarios", data={
        "name": "Nova Administradora", "email": "nova-admin@example.com",
        "password": "senha-segura", "password_confirm": "senha-segura",
        "role": "administrador", "is_primary": "yes", "csrf_token": csrf(client),
    })
    premature = client.post("/painel/configuracoes/usuarios/desativar-legado",
                            data={"csrf_token": csrf(client)}, follow_redirects=True)
    assert "confirme um login" in premature.text
    client.post("/sair", data={"csrf_token": csrf(client)})
    logged = client.post("/login", data={"user": "nova-admin@example.com",
                                          "password": "senha-segura", "csrf_token": csrf(client)})
    assert logged.status_code == 302 and logged.headers["Location"].endswith("/painel")
    disabled = client.post("/painel/configuracoes/usuarios/desativar-legado",
                           data={"csrf_token": csrf(client)}, follow_redirects=True)
    assert "anterior excluído e bloqueado" in disabled.text
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT legacy_staff_active FROM settings WHERE id=1").fetchone()[0] == 0


def test_staff_recovery_rejects_whatsapp_channel(tmp_path, monkeypatch):
    import app as app_module
    sent = []
    monkeypatch.setattr(app_module, "send_code", lambda *args: (sent.append(args) or (True, "id")))
    app = create_app({"TESTING": True, "SECRET_KEY": "test", "DATABASE": str(tmp_path / "app.db")})
    client = app.test_client()
    response = client.post("/equipe/recuperar-senha", data={
        "stage": "request", "email": "equipe@example.com", "channel": "whatsapp",
        "csrf_token": csrf(client),
    })
    assert response.status_code == 400
    assert "somente por e-mail" in response.text
    assert sent == []


def test_blog_formatter_supports_structure_without_scripts():
    rendered = format_blog_body("## Como funciona\n\n- Primeiro passo\n- Segundo passo\n\n<script>alert(1)</script>")
    assert "<h2>Como funciona</h2>" in rendered
    assert "<ul><li>Primeiro passo</li><li>Segundo passo</li></ul>" in rendered
    assert "<script>" not in rendered and "&lt;script&gt;" in rendered
