import io
import sqlite3
from urllib.parse import parse_qs, urlparse

import pytest
from PIL import Image

from app import create_app
from test_app import register
from test_customization import csrf, legacy_login


@pytest.fixture
def users_app(tmp_path):
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': str(tmp_path / 'tabs.db')})
    client = app.test_client()
    register(client, 'Cliente Exclusivo')
    legacy_login(client)
    client.post('/painel/configuracoes/usuarios', data={
        'name': 'Gerente Exclusivo', 'email': 'gerente@example.com',
        'password': 'senha-segura', 'password_confirm': 'senha-segura',
        'role': 'gerente', 'can_clients': 'yes', 'csrf_token': csrf(client),
    })
    return app, client


def test_tabs_separate_accounts_and_search_by_email_id_and_role(users_app):
    app, client = users_app
    clients = client.get('/painel/configuracoes/usuarios').text
    assert 'Cliente Exclusivo' in clients and 'Gerente Exclusivo' not in clients
    assert 'Adicionar cliente' in clients and 'Adicionar membro da equipe' not in clients
    team = client.get('/painel/configuracoes/usuarios?tab=equipe').text
    assert 'Gerente Exclusivo' in team and 'Cliente Exclusivo' not in team
    assert 'Adicionar membro da equipe' in team and 'Adicionar cliente' not in team
    for query in ('gerente@example.com', 'GERPCA-1', 'gerente'):
        assert 'Gerente Exclusivo' in client.get('/painel/configuracoes/usuarios', query_string={'tab': 'equipe', 'q_team': query, 'q_clients': 'ausente'}).text
    for query in ('cliente@example.com', 'CLIPCA-1'):
        assert 'Cliente Exclusivo' in client.get('/painel/configuracoes/usuarios', query_string={'q_clients': query, 'q_team': 'ausente'}).text
    assert 'Nenhum membro da equipe encontrado' in client.get('/painel/configuracoes/usuarios?tab=equipe&q_team=ausente').text
    assert 'Nenhum cliente encontrado' in client.get('/painel/configuracoes/usuarios?q_clients=ausente').text


def test_actions_keep_team_tab_search_and_existing_codes(users_app):
    app, client = users_app
    url = '/painel/configuracoes/usuarios/1/alternar?tab=equipe&q_team=gerente&q_clients=cliente'
    response = client.post(url, data={'csrf_token': csrf(client)})
    query = parse_qs(urlparse(response.location).query)
    assert query['tab'] == ['equipe'] and query['q_team'] == ['gerente'] and query['q_clients'] == ['cliente']
    page = client.get(response.location).text
    assert 'Gerente Exclusivo' in page and 'Desativado' in page
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT public_id,active FROM staff_users').fetchone() == ('GERPCA-1', 0)
        assert db.execute('SELECT public_id FROM clients').fetchone() == ('CLIPCA-1',)


@pytest.mark.parametrize('role', ['gerente', 'atendente'])
def test_tabs_and_actions_do_not_grant_management_to_operational_staff(users_app, role):
    app, client = users_app
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.execute('UPDATE staff_users SET role=?', (role,))
    with client.session_transaction() as session:
        session['staff_user_id'] = 1
        session['staff_session_version'] = 1
        session.pop('staff_legacy', None)
    for tab in ('clientes', 'equipe'):
        assert client.get('/painel/configuracoes/usuarios', query_string={'tab': tab}).status_code == 403
    assert client.post('/painel/configuracoes/usuarios/1/alternar', data={'csrf_token': csrf(client)}).status_code == 403
    assert client.get('/painel/configuracoes/banner-principal').status_code == 403


def test_banner_dedicated_settings_preserve_original_and_other_images(users_app):
    app, client = users_app
    out = io.BytesIO()
    Image.new('RGB', (2170, 725), 'orange').save(out, 'PNG')
    original = out.getvalue()
    response = client.post('/painel/configuracoes/banner-principal', data={
        'main_banner_image': (io.BytesIO(original), 'original.png'), 'csrf_token': csrf(client),
    }, content_type='multipart/form-data', follow_redirects=True)
    assert response.status_code == 200 and '2048 × 684' in response.text
    assert client.get('/identidade/banner-principal').data == original
    with sqlite3.connect(app.config['DATABASE']) as db:
        assert db.execute('SELECT header_image FROM settings').fetchone() == (None,)
        assert db.execute('SELECT COUNT(*) FROM site_banners').fetchone() == (0,)
    bad = client.post('/painel/configuracoes/banner-principal', data={
        'main_banner_image': (io.BytesIO(b'<svg/>'), 'fake.png'), 'csrf_token': csrf(client),
    }, content_type='multipart/form-data')
    assert bad.status_code == 400
    assert client.get('/identidade/banner-principal').data == original


def test_default_service_banner_and_original_whatsapp_asset(users_app):
    app, client = users_app
    banner = client.get('/identidade/banner-principal', follow_redirects=True)
    assert banner.status_code == 200 and banner.mimetype == 'image/webp'
    with Image.open(io.BytesIO(banner.data)) as image:
        assert image.width >= 2048 and abs(image.width / image.height - 3) < .02
        assert len(banner.data) <= 900 * 1024
    home = client.get('/').text
    support = home.split('class="support-float', 1)[1].split('</a>', 1)[0]
    assert 'whatsapp-support.webp' in support and '<img' in support and '<svg' not in support
    assert 'https://wa.me/5516988004966?text=Ol%C3%A1%21%20Preciso%20de%20suporte%20com%20o%20Me%20Guarde.' in support
    image = client.get('/static/whatsapp-support.webp')
    assert image.status_code == 200 and image.mimetype == 'image/webp'
    assert len(image.data) < 4000
    with Image.open(io.BytesIO(image.data)) as icon:
        assert icon.size == (112, 112)


def test_team_pagination_keeps_search_and_all_accounts_accessible(users_app):
    app, client = users_app
    with sqlite3.connect(app.config['DATABASE']) as db:
        db.executemany(
            'INSERT INTO staff_users(public_id,name,email,password_hash,role,created_at) VALUES(?,?,?,?,?,?)',
            [(f'ATEPCA-{n}', f'Membro {n:02}', f'membro{n}@example.com', 'unused-test-hash', 'atendente', '2026-10-24T00:00:00') for n in range(51)],
        )
    first = client.get('/painel/configuracoes/usuarios?tab=equipe&q_team=membro').text
    second = client.get('/painel/configuracoes/usuarios?tab=equipe&q_team=membro&page=2').text
    assert 'Página 1 de 2' in first and 'Página 2 de 2' in second
    assert 'ATEPCA-50' not in first and 'ATEPCA-50' in second
    assert 'value="membro"' in second and 'Membro 00' in first
    assert 'Gerente Exclusivo' not in first and 'Cliente Exclusivo' not in second
