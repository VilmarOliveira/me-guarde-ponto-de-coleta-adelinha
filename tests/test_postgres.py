"""Smoke test opcional para PostgreSQL/Neon.

Defina TEST_DATABASE_URL somente em CI ou em um projeto Neon de teste. O teste
cria apenas estruturas ausentes e nunca remove tabelas ou registros.
"""
import os

import pytest

pytest.importorskip("flask")
pytest.importorskip("psycopg")

from app import CLIENT_COLUMNS, DatabaseConnection, create_app


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL não configurada")
def test_neon_schema_and_queries_are_postgresql_compatible():
    database_url = os.environ["TEST_DATABASE_URL"]
    app = create_app({"TESTING": True, "SECRET_KEY": "postgres-test", "DATABASE": database_url})
    client = app.test_client()
    assert client.get("/health").json == {"status": "ok"}

    connection = DatabaseConnection(database_url)
    assert connection.postgres
    columns = connection.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='clients'"
    ).fetchall()
    assert set(CLIENT_COLUMNS.split(",")) <= {row["column_name"] for row in columns}
    connection.close()


@pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL não configurada')
def test_banner_and_pix_settings_persist_on_postgresql():
    import io
    from PIL import Image

    database_url = os.environ['TEST_DATABASE_URL']
    config = {'TESTING': True, 'SECRET_KEY': 'postgres-settings', 'DATABASE': database_url}
    app = create_app(config)
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(staff='postgres-test', staff_legacy=True, csrf_token='pg-csrf')
    output = io.BytesIO()
    Image.new('RGB', (2048, 684), 'orange').save(output, 'PNG')
    image = output.getvalue()
    response = client.post('/painel/configuracoes/banners', data={
        'banner_id': '0', 'position': '1', 'alt_text': 'Banner PostgreSQL', 'active': 'yes',
        'image': (io.BytesIO(image), 'pg.png'), 'csrf_token': 'pg-csrf'})
    assert response.status_code == 302
    response = client.post('/painel/configuracoes/pix', data={
        'receiver': 'Titular PostgreSQL', 'pix_key_type': 'E-mail',
        'pix_key': 'postgres@example.com', 'csrf_token': 'pg-csrf'})
    assert response.status_code == 302
    restarted = create_app(config).test_client()
    assert restarted.get('/identidade/banner-principal').data == image
    assert 'Banner PostgreSQL' in restarted.get('/').text
    connection = DatabaseConnection(database_url)
    row = connection.execute('SELECT pix_key,pix_receiver_name FROM settings WHERE id=1').fetchone()
    assert row == {'pix_key': 'postgres@example.com', 'pix_receiver_name': 'Titular PostgreSQL'}
    connection.close()


@pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL não configurada')
def test_client_prefix_migration_on_postgresql():
    from app import next_public_code

    database_url = os.environ['TEST_DATABASE_URL']
    config = {'TESTING': True, 'SECRET_KEY': 'postgres-codes', 'DATABASE': database_url}
    create_app(config)
    connection = DatabaseConnection(database_url)
    rows = connection.execute("SELECT last_value FROM id_sequences WHERE prefix IN ('CLTPCA','CLIPCA')").fetchall()
    number = max((row['last_value'] for row in rows), default=0) + 10
    old_code, new_code = f'CLTPCA-{number}', f'CLIPCA-{number}'
    client_id = connection.execute('''INSERT INTO clients(public_id,name,cpf,phone,terms_version,signature,accepted_at,contract_hash)
        VALUES(?,?,?,?,?,?,?,?) RETURNING id''',
        (old_code, 'Cliente migração', '00000000000', '16999999999', 'v1', 'assinatura', '2026-01-01', 'hash-original')).fetchone()['id']
    connection.commit()
    connection.close()
    create_app(config)
    connection = DatabaseConnection(database_url)
    row = connection.execute('SELECT public_id,legacy_public_id,contract_public_id,contract_hash FROM clients WHERE id=?', (client_id,)).fetchone()
    assert row == {'public_id': new_code, 'legacy_public_id': old_code,
                   'contract_public_id': old_code, 'contract_hash': 'hash-original'}
    assert next_public_code(connection, 'CLIPCA') == f'CLIPCA-{number+1}'
    connection.rollback()
    connection.close()


@pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL não configurada')
def test_explanatory_cards_persist_on_postgresql():
    import io
    from PIL import Image
    config = {'TESTING': True, 'SECRET_KEY': 'pg-cards', 'DATABASE': os.environ['TEST_DATABASE_URL']}
    app = create_app(config)
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(staff='postgres-test', staff_legacy=True, csrf_token='pg-cards-csrf')
    output = io.BytesIO()
    Image.new('RGB', (800, 1200), 'orange').save(output, 'PNG')
    content = output.getvalue()
    response = client.post('/painel/configuracoes/cartoes-explicativos', data={
        'card_id': '4', 'title': 'Aguarde e retire', 'alt_text': 'Cartão persistido no PostgreSQL',
        'image': (io.BytesIO(content), 'cartao.png'), 'csrf_token': 'pg-cards-csrf'})
    assert response.status_code == 302
    restarted = create_app(config)
    with restarted.test_client() as visitor:
        assert 'Cartão persistido no PostgreSQL' in visitor.get('/').text
        image = visitor.get('/cartoes-explicativos/4/original', follow_redirects=True)
        assert image.data == content
        for variant in ('celular', 'computador'):
            thumbnail = visitor.get(f'/cartoes-explicativos/4/{variant}', follow_redirects=True)
            assert thumbnail.mimetype == 'image/webp'
            with Image.open(io.BytesIO(thumbnail.data)) as decoded:
                assert decoded.format == 'WEBP'


@pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL não configurada')
def test_google_oauth_schema_and_single_use_flow_on_postgresql(monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    from test_customization import csrf
    config={'TESTING':True,'SECRET_KEY':'pg-oauth-fixture','DATABASE':os.environ['TEST_DATABASE_URL'],
            'GOOGLE_LOGIN_ENABLED':'1','GOOGLE_LOGIN_CLIENT_ID':'pg-test-client','GOOGLE_LOGIN_CLIENT_SECRET':'fake-test-secret',
            'GOOGLE_LOGIN_REDIRECT_URI':'https://pontodecoletaadl.servehttp.com/cliente/google/retorno'}
    app=create_app(config)
    client=app.test_client()
    response=client.post('/cliente/google/iniciar',data={'csrf_token':csrf(client)})
    assert response.status_code==302
    state=parse_qs(urlsplit(response.location).query)['state'][0]
    response=client.get('/cliente/google/retorno',query_string={'state':state,'error':'access_denied'},follow_redirects=True)
    assert 'cancelada' in response.text
    connection=DatabaseConnection(config['DATABASE'])
    assert connection.execute('SELECT count(*) total FROM google_oauth_flows').fetchone()['total']==0
    # Persist a fixture identity and pending link against an existing test client.
    row=connection.execute('SELECT id FROM clients ORDER BY id LIMIT 1').fetchone()
    assert row
    connection.execute('INSERT INTO google_client_identities(google_sub,client_id,email_at_link,linked_at) VALUES(?,?,?,?)',('pg-test-subject',row['id'],'pg@example.test',1))
    connection.execute('INSERT INTO google_login_pending(id_hash,google_sub,email,client_id,session_version,expires_at) VALUES(?,?,?,?,?,?)',('pg-ticket','pg-test-other','pg@example.test',row['id'],1,9999999999))
    connection.commit();connection.close()
    create_app(config)
    connection=DatabaseConnection(config['DATABASE'])
    assert connection.execute('SELECT client_id FROM google_client_identities WHERE google_sub=?',('pg-test-subject',)).fetchone()['client_id']==row['id']
    assert connection.execute('DELETE FROM google_login_pending WHERE id_hash=? RETURNING google_sub',('pg-ticket',)).fetchone()['google_sub']=='pg-test-other'
    connection.commit();connection.close()

    # Exercise first-link/password confirmation with PostgreSQL transactions.
    from test_app import register
    import google_login
    assert register(client).status_code == 200
    connection=DatabaseConnection(config['DATABASE'])
    connection.execute("UPDATE clients SET confirmation_channel='email',confirmation_destination=email,confirmation_validated_at=accepted_at WHERE email='cliente@example.com'")
    connection.commit();connection.close()
    monkeypatch.setattr(google_login, 'verified_identity', lambda *args: ('pg-linked-real-flow', 'cliente@example.com'))
    response=client.post('/cliente/google/iniciar',data={'csrf_token':csrf(client)})
    state=parse_qs(urlsplit(response.location).query)['state'][0]
    response=client.get('/cliente/google/retorno',query_string={'state':state,'code':'test-only-code'})
    assert response.location.endswith('/cliente/google/confirmar')
    response=client.post('/cliente/google/confirmar',data={'password':'senha-segura','csrf_token':csrf(client)})
    assert response.location == '/cliente'
    with client.session_transaction() as session:
        assert session.get('client_id') and not session.get('staff')
