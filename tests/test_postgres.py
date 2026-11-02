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


@pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL não configurada')
def test_regularization_append_only_records_and_email_proof_postgresql(monkeypatch):
    import re
    import app as application
    from test_customization import csrf
    from test_app import signature
    from app import next_public_code
    config={'TESTING':True,'SECRET_KEY':'pg-regularization','DATABASE':os.environ['TEST_DATABASE_URL']}
    app=create_app(config);client=app.test_client();captured={}
    monkeypatch.setattr(application,'send_code',lambda channel,email,code:(captured.update(code=code) or True,'test-only'))
    connection=DatabaseConnection(config['DATABASE'])
    source=connection.execute("SELECT * FROM clients WHERE email='cliente@example.com'").fetchone()
    assert source
    connection.execute('UPDATE clients SET confirmation_validated_at=NULL WHERE id=?',(source['id'],));connection.commit()
    assert client.post('/cliente/login',data={'cpf':'cliente@example.com','password':'senha-segura','csrf_token':csrf(client)}).location=='/cliente'
    def complete(new_signature=None):
        page=client.get('/cliente/pendencias')
        scope=re.search(r'name="scope" value="([a-f0-9]+)"',page.text).group(1)
        assert client.post('/cliente/pendencias',data={'action':'send_code','scope':scope,'csrf_token':csrf(client)}).status_code==200
        data={'action':'confirm','scope':scope,'terms_accept':'yes','code':captured['code'],'csrf_token':csrf(client)}
        if new_signature:data['signature']=new_signature
        result=client.post('/cliente/pendencias',data=data,follow_redirects=True)
        assert result.status_code==200 and 'Dados atualizados com sucesso.' in result.text
        assert result.request.path=='/cliente/pendencias-e-autorizacoes'
    complete()
    assert connection.execute('SELECT count(*) n FROM client_contract_email_confirmations WHERE client_id=?',(source['id'],)).fetchone()['n']==1
    assert connection.execute('SELECT confirmation_validated_at FROM clients WHERE id=?',(source['id'],)).fetchone()['confirmation_validated_at'] is None
    # A separate incomplete client must explicitly sign a new, independent record.
    values={k:v for k,v in source.items() if k!='id'}
    values.update(public_id=next_public_code(connection,'CLIPCA'),cpf='98765432100',email='pg-missing@example.test',signature='')
    columns=','.join(values);placeholders=','.join('?' for _ in values)
    owner=connection.execute(f'INSERT INTO clients({columns}) VALUES({placeholders}) RETURNING id',tuple(values.values())).fetchone()['id'];connection.commit()
    with client.session_transaction() as session:session.update(client_id=owner,client_session_version=values['session_version'])
    complete(signature())
    snapshots=connection.execute('SELECT source,signature FROM client_contract_records WHERE client_id=? ORDER BY id',(owner,)).fetchall()
    assert len(snapshots)==2 and snapshots[0]['source']=='legacy' and snapshots[0]['signature']==''
    assert snapshots[1]['source']=='acceptance' and snapshots[1]['signature']==signature()
    assert connection.execute('SELECT signature FROM clients WHERE id=?',(owner,)).fetchone()['signature']==''
    connection.close()


@pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL não configurada')
def test_client_actions_publication_response_versioning_and_restart_postgresql(monkeypatch):
    import re
    import app as application
    from test_customization import csrf,legacy_login
    from test_app import signature
    config={'TESTING':True,'SECRET_KEY':'pg-actions','DATABASE':os.environ['TEST_DATABASE_URL']}
    app=create_app(config);admin=app.test_client();client=app.test_client();legacy_login(admin)
    captured={};monkeypatch.setattr(application,'send_code',lambda channel,email,code:(captured.update(code=code) or True,'test-only'))
    connection=DatabaseConnection(config['DATABASE'])
    owner=connection.execute("SELECT * FROM clients WHERE email='cliente@example.com'").fetchone()
    assert owner
    with client.session_transaction() as sess:sess.update(client_id=owner['id'],client_session_version=owner['session_version'])
    path='/painel/configuracoes/pendencias';hub='/cliente/pendencias-e-autorizacoes'
    result=admin.post(path,data={'title':'PG confirmação','explanation':'Teste isolado','body':'Versão apresentada PG','kind':'mandatory','audience':'all','actions':['signature','email'],'functions':['delivery'],'csrf_token':csrf(admin)})
    rid=int(re.search(r'/pendencias/(\d+)',result.location).group(1))
    row=connection.execute('SELECT * FROM client_action_requests WHERE id=?',(rid,)).fetchone()
    assert row['status']=='draft'
    assert admin.post(f'{path}/{rid}/publicar',data={'content_hash':row['content_hash'],'confirm':'yes','csrf_token':csrf(admin)}).status_code==302
    assert client.get('/cliente/dados-de-entrega').status_code==403
    page=client.get(f'{hub}/{rid}');scope=re.search(r'name="scope" value="([a-f0-9]+)"',page.text).group(1)
    assert client.post(f'{hub}/{rid}',data={'scope':scope,'choice':'send_code','csrf_token':csrf(client)}).status_code==200
    assert client.post(f'{hub}/{rid}',data={'scope':scope,'choice':'accept','signature':signature(),'code':captured['code'],'confirmed':'yes','csrf_token':csrf(client)}).status_code==302
    answer=dict(connection.execute('SELECT * FROM client_action_responses WHERE request_id=? AND client_id=?',(rid,owner['id'])).fetchone())
    assert answer['body']==row['body'] and answer['signature']==signature() and answer['email_confirmed_at']
    assert client.get('/cliente/dados-de-entrega').status_code==200
    clone=admin.post(f'{path}/{rid}/nova-versao',data={'csrf_token':csrf(admin)})
    newer=int(clone.location.rsplit('/',1)[1]);assert newer!=rid
    assert connection.execute('SELECT version FROM client_action_requests WHERE id=?',(newer,)).fetchone()['version']==2
    admin.post(f'{path}/{rid}/cancelar',data={'csrf_token':csrf(admin)})
    restarted=create_app(config).test_client()
    with restarted.session_transaction() as sess:sess.update(client_id=owner['id'],client_session_version=owner['session_version'])
    assert restarted.get(f'{hub}/{rid}').status_code==200
    assert dict(connection.execute('SELECT * FROM client_action_responses WHERE id=?',(answer['id'],)).fetchone())==answer
    connection.close()


@pytest.mark.skipif(not os.environ.get('TEST_DATABASE_URL'), reason='TEST_DATABASE_URL não configurada')
def test_reaccept_current_contract_preserves_old_pg_evidence(monkeypatch):
    import app as application
    from test_customization import csrf,legacy_login
    from test_app import signature
    from client_compliance import revision_id
    from client_actions import current_contract,client_items
    from contract import CONTRACT_VERSION,CONTRACT_LABEL
    from app import terms_text
    from datetime import datetime
    from core import TZ
    config={'TESTING':True,'SECRET_KEY':'pg-reaccept','DATABASE':os.environ['TEST_DATABASE_URL']}
    app=create_app(config);admin=app.test_client();client=app.test_client();legacy_login(admin)
    db=DatabaseConnection(config['DATABASE']);owner=db.execute("SELECT * FROM clients WHERE email='cliente@example.com'").fetchone()
    assert owner
    from app import next_public_code
    values={k:v for k,v in owner.items() if k!='id'}
    public_id=next_public_code(db,'CLIPCA')
    values.update(public_id=public_id,contract_public_id=public_id,cpf='11144477735',email='reaccept@example.test',terms_version='CONTRATO-2026-v2',terms_text='Texto antigo em PG',contract_revision_id=None,confirmation_channel='email',confirmation_destination='reaccept@example.test',confirmation_validated_at=owner['accepted_at'])
    owner_id=db.execute('INSERT INTO clients('+','.join(values)+') VALUES('+','.join('?' for _ in values)+') RETURNING id',tuple(values.values())).fetchone()['id'];db.commit()
    owner=db.execute('SELECT * FROM clients WHERE id=?',(owner_id,)).fetchone();before=dict(owner)
    with client.session_transaction() as sess:sess.update(client_id=owner['id'],client_session_version=owner['session_version'])
    default=(CONTRACT_VERSION,CONTRACT_LABEL,terms_text());version,label,text=current_contract(db,default)
    result=admin.post('/painel/configuracoes/pendencias/contrato-vigente/exigir-aceite',data={'summary':'Exigir explicitamente o aceite do texto vigente em PG','expected_revision':revision_id(version,text),'require_existing':'yes','csrf_token':csrf(admin)})
    rid=int(result.location.split('/')[-2]);row=db.execute('SELECT * FROM client_action_requests WHERE id=?',(rid,)).fetchone()
    admin.post(f'/painel/configuracoes/pendencias/{rid}/publicar',data={'content_hash':row['content_hash'],'confirm':'yes','csrf_token':csrf(admin)})
    assert any(x['id']==rid for x in client_items(db,owner,datetime.now(TZ).isoformat()))
    import re
    page=client.get(f'/cliente/pendencias-e-autorizacoes/{rid}');scope=re.search(r'name="scope" value="([a-f0-9]+)"',page.text).group(1)
    captured={};monkeypatch.setattr(application,'send_code',lambda channel,email,code:(captured.update(code=code) or True,'fixture'))
    client.post(f'/cliente/pendencias-e-autorizacoes/{rid}',data={'choice':'send_code','scope':scope,'csrf_token':csrf(client)})
    assert client.post(f'/cliente/pendencias-e-autorizacoes/{rid}',data={'choice':'accept','scope':scope,'confirmed':'yes','signature':signature(),'code':captured['code'],'csrf_token':csrf(client)}).status_code==302
    assert not any(x['id']==rid for x in client_items(db,owner,datetime.now(TZ).isoformat()))
    assert dict(db.execute('SELECT * FROM clients WHERE id=?',(owner['id'],)).fetchone())==before
    accepted=db.execute("SELECT * FROM client_contract_records WHERE client_id=? AND source='acceptance' ORDER BY id DESC LIMIT 1",(owner['id'],)).fetchone()
    assert accepted['contract_revision_id']==revision_id(version,text)
    assert accepted['signature'] and accepted['confirmation_validated_at']
    db.close()
