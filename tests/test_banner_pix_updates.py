import io
import re
import sqlite3

import pytest
from PIL import Image

from app import BANNER_MAX_BYTES, PIX_KEY, create_app, validate_pix_settings
from test_app import register, signature
from test_customization import csrf, legacy_login


@pytest.fixture
def configured(tmp_path):
    database = str(tmp_path / 'updates.db')
    app = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': database})
    client = app.test_client()
    legacy_login(client)
    return app, client, database


def image_bytes(kind='PNG'):
    output = io.BytesIO()
    Image.new('RGB', (2048, 684), 'orange').save(output, kind)
    return output.getvalue()


def save_banner(client, banner_id=0, **overrides):
    data = {'banner_id': str(banner_id), 'position': '1', 'alt_text': 'Banner horizontal',
            'active': 'yes', 'csrf_token': csrf(client)}
    data.update(overrides)
    return client.post('/painel/configuracoes/banners', data=data, follow_redirects=True)


@pytest.mark.parametrize('kind', ['PNG', 'JPEG', 'WEBP'])
def test_banner_format_size_boundary_and_preserved_previous(configured, kind):
    app, client, database = configured
    image = image_bytes(kind)
    exact = image + b'\0' * (BANNER_MAX_BYTES - len(image))
    assert save_banner(client, image=(io.BytesIO(exact), 'valid.' + kind.lower())).status_code == 200
    assert client.get('/identidade/banner-principal').data == exact
    for invalid in (exact + b'0', b'not an image', b'', b'GIF89a' + b'0' * 50):
        result = save_banner(client, image=(io.BytesIO(invalid), 'fake.png'), alt_text='Do not save')
        assert result.status_code == 400
        assert '900 KB' in result.text and 'imagem anterior foi preservada' in result.text
        assert client.get('/identidade/banner-principal').data == exact
        with sqlite3.connect(database) as db:
            assert db.execute('SELECT main_banner_alt FROM settings').fetchone()[0] == 'Banner horizontal'


def test_banner_order_activation_single_and_restart(configured):
    app, client, database = configured
    for index in (2, 3):
        assert save_banner(client, -1, position=str(index), alt_text=f'Imagem {index}',
                           image=(io.BytesIO(image_bytes()), 'banner.png')).status_code == 200
    assert save_banner(client, 2, position='1', alt_text='Terceira primeiro').status_code == 200
    home = client.get('/').text
    assert home.count('data-carousel role=') == 1
    assert home.index('alt="Terceira primeiro"') < home.index('alt="ME GUARDE')
    assert home.count(' data-slide aria-hidden=') == 3
    assert 'data-slide-to="0"' in home
    assert 'data-carousel-next' not in home and 'data-carousel-prev' not in home
    assert 'data-carousel-pause' not in home
    for banner_id, position in ((1, '2'), (2, '1')):
        assert save_banner(client, banner_id, position=position, active='no').status_code == 200
    restarted = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': database}).test_client()
    home = restarted.get('/').text
    assert home.count(' data-slide aria-hidden=') == 1
    assert 'data-carousel-pause' not in home and 'data-carousel-next' not in home
    assert save_banner(client, 0, position='3', active='no').status_code == 200
    assert 'data-carousel role=' not in client.get('/').text


def test_migration_preserves_all_legacy_images_and_is_repeatable(configured):
    app, client, database = configured
    image = image_bytes()
    with sqlite3.connect(database) as db:
        db.execute('UPDATE settings SET main_banner_image=?,main_banner_mime=?,pix_receiver_name=?', (image, 'image/png', 'Titular existente'))
        for col in ('main_banner_position', 'main_banner_active', 'main_banner_alt'):
            db.execute(f'ALTER TABLE settings DROP COLUMN {col}')
        db.execute('ALTER TABLE site_banners DROP COLUMN active')
        for position in (1, 2, 3):
            db.execute('INSERT INTO site_banners(image,mime_type,alt_text,position,updated_at) VALUES(?,?,?,?,?)',
                       (image, 'image/png', f'Legado {position}', position, 'original'))
    for _ in range(2):
        restored = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': database}).test_client()
        assert restored.get('/identidade/banner-principal').data == image
        assert restored.get('/').text.count(' data-slide aria-hidden=') == 3
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT position,active FROM site_banners ORDER BY position').fetchall() == [(2, 1), (3, 1), (4, 0)]
        assert all(row[0] == image for row in db.execute('SELECT image FROM site_banners'))
        assert db.execute('SELECT pix_receiver_name FROM settings').fetchone()[0] == 'Titular existente'
    legacy_login(restored)
    assert 'Legado 3' in restored.get('/painel/configuracoes/banners').text
    assert save_banner(restored, 3, position='4').status_code == 400


@pytest.mark.parametrize('role', ['gerente', 'atendente'])
def test_admin_only_even_with_settings_permission(configured, role):
    app, client, database = configured
    client.post('/painel/configuracoes/usuarios', data={
        'name': 'Operador', 'email': 'operador@example.com', 'password': 'senha-segura',
        'password_confirm': 'senha-segura', 'role': role, 'csrf_token': csrf(client)})
    with sqlite3.connect(database) as db:
        db.execute('UPDATE staff_users SET can_settings=1')
        staff_id = db.execute('SELECT id FROM staff_users').fetchone()[0]
    with client.session_transaction() as session:
        session['staff_user_id'] = staff_id
        session['staff_session_version'] = 1
        session.pop('staff_legacy', None)
    for path in ('/painel/configuracoes/pix', '/painel/configuracoes/banners', '/painel/configuracoes/banner-principal'):
        assert client.get(path).status_code == 403
        assert client.post(path, data={'csrf_token': csrf(client)}).status_code == 403
    assert client.post('/painel/configuracoes/identidade', data={
        'action': 'main_banner', 'csrf_token': csrf(client)}).status_code == 403


@pytest.mark.parametrize('kind,key,expected', [
    ('CPF', '529.982.247-25', '52998224725'), ('CNPJ', '38.145.273/0001-05', PIX_KEY),
    ('E-mail', 'Pix@Example.com', 'pix@example.com'), ('Telefone', '+5516999999999', '+5516999999999'),
    ('Aleatória', '123e4567-e89b-12d3-a456-426614174000', '123e4567-e89b-12d3-a456-426614174000'),
])
def test_pix_valid_types(kind, key, expected):
    assert validate_pix_settings('Titular válido', kind, key) == expected


@pytest.mark.parametrize('receiver,kind,key', [('', 'CNPJ', PIX_KEY), ('😀', 'CPF', '52998224725'),
    ('Nome', 'CNPJ', '38145273000106'), ('Nome', 'CPF', '11111111111'),
    ('Nome', 'Telefone', '16999999999'), ('Nome', 'E-mail', 'invalido'),
    ('Nome', 'Aleatória', 'nao-e-uuid'), ('Nome', 'outro', 'abc'),
    ('Nome', 'E-mail', 'a' * 70 + '@example.com')])
def test_invalid_pix_fields_preserve_database(configured, receiver, kind, key):
    app, client, database = configured
    response = client.post('/painel/configuracoes/pix', data={
        'receiver': receiver, 'pix_key_type': kind, 'pix_key': key, 'csrf_token': csrf(client)})
    assert response.status_code == 400
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT pix_key FROM settings').fetchone()[0] == PIX_KEY


def test_pix_edit_qr_history_and_restart(configured):
    app, client, database = configured
    register(client)
    client.post('/pacotes', data={'client_id': '1', 'tracking': 'TESTPIX', 'shelf': 'A1', 'width': '20', 'height': '20', 'length': '20', 'weight': '1', 'csrf_token': csrf(client)})
    client.post('/pacotes/1/avisar', data={'csrf_token': csrf(client)})
    def update(key, receiver):
        response = client.post('/painel/configuracoes/pix', data={'receiver': receiver,
            'pix_key_type': 'CNPJ', 'pix_key': key, 'csrf_token': csrf(client)}, follow_redirects=True)
        assert 'Dados do Pix atualizados com sucesso!' in response.text
    update('11222333000181', 'Primeiro titular com um nome muito longo')
    summary = client.post('/painel/retirada/resumo', data={'package_ids': '1', 'csrf_token': csrf(client)})
    assert summary.status_code == 200
    assert '11222333000181' in summary.json['pix_code']
    assert '5925' + 'PRIMEIRO TITULAR COM UM NOME MUITO LONGO'[:25] in summary.json['pix_code']
    assert summary.json['qr_code'].startswith('data:image/png;base64,')
    result = client.post('/retiradas', data={'package_ids': '1', 'receiver_name': 'Cliente Teste',
        'receiver_document': '52998224725', 'document_type': 'CPF', 'payment_method': 'Pix AI',
        'payment_confirmed': 'yes', 'pickup_signature': signature(), 'csrf_token': csrf(client)})
    assert result.status_code == 302
    pdf_before = client.get('/comprovantes/1/pacotes/1.pdf').data
    with sqlite3.connect(database) as db:
        before = db.execute('SELECT * FROM pickups').fetchall()
    update('38145273000105', 'Segundo titular')
    restarted = create_app({'TESTING': True, 'SECRET_KEY': 'test', 'DATABASE': database}).test_client()
    legacy_login(restarted)
    assert '38145273000105' in restarted.get('/painel/configuracoes/pix?editar=1').text
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT * FROM pickups').fetchall() == before
        assert db.execute('SELECT pix_key_snapshot FROM pickups').fetchone()[0] == '11222333000181'
    restarted.post('/pacotes', data={'client_id': '1', 'tracking': 'TESTPIX2', 'shelf': 'A2',
        'width': '20', 'height': '20', 'length': '20', 'weight': '1', 'csrf_token': csrf(restarted)})
    restarted.post('/pacotes/2/avisar', data={'csrf_token': csrf(restarted)})
    summary_after = restarted.post('/painel/retirada/resumo', data={'package_ids': '2', 'csrf_token': csrf(restarted)})
    assert summary_after.status_code == 200
    assert '38145273000105' in summary_after.json['pix_code']
    assert '11222333000181' not in summary_after.json['pix_code']
    pdf_after = restarted.get('/comprovantes/1/pacotes/1.pdf').data
    assert b'11222333000181' in pdf_after and b'38145273000105' not in pdf_after
    assert re.findall(rb'stream(.*?)endstream', pdf_after, re.S) == re.findall(rb'stream(.*?)endstream', pdf_before, re.S)
