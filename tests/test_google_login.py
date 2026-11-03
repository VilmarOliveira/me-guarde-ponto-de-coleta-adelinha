import hashlib
import io
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
from google.auth import crypt, jwt
from google.oauth2 import id_token

import google_login as google
from app import create_app
from test_app import register
from test_customization import csrf

CONFIG = {'TESTING': True, 'SECRET_KEY': 'test-google-session', 'GOOGLE_LOGIN_ENABLED': '1',
          'GOOGLE_LOGIN_CLIENT_ID': 'test-client.apps.googleusercontent.com', 'GOOGLE_LOGIN_CLIENT_SECRET': 'not-a-real-secret',
          'GOOGLE_LOGIN_REDIRECT_URI': 'https://pontodecoletaadl.servehttp.com/cliente/google/retorno'}

@pytest.fixture(scope='module')
def signing_keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    return public, crypt.RSASigner.from_string(pem)

@pytest.fixture
def site(tmp_path, monkeypatch, signing_keys):
    database = str(tmp_path/'google.db')
    app = create_app({**CONFIG, 'DATABASE': database})
    client = app.test_client()
    register(client)
    with sqlite3.connect(database) as db:
        db.execute("UPDATE clients SET confirmation_channel='email',confirmation_destination=email,confirmation_validated_at=accepted_at")
    public, signer = signing_keys
    monkeypatch.setattr(id_token, '_fetch_certs', lambda *a,**k: {'test-key':public.decode()})
    state = {'claims':{},'exchange_count':0}
    def exchange(url, data, timeout, allow_redirects):
        assert url=='https://oauth2.googleapis.com/token' and timeout==10 and allow_redirects is False
        with sqlite3.connect(database) as db:
            assert not db.execute('SELECT 1 FROM google_oauth_flows').fetchone()  # consumed before exchange
        assert hashlib.sha256(data['code_verifier'].encode()).hexdigest()==state['verifier_hash']
        state['exchange_count']+=1
        claims={'iss':'https://accounts.google.com','aud':CONFIG['GOOGLE_LOGIN_CLIENT_ID'],'sub':'google-subject-123',
                'email':'cliente@example.com','email_verified':True,'iat':int(time.time()),'exp':int(time.time())+300,
                'nonce':state['nonce'],**state['claims']}
        token = jwt.encode(signer,claims,key_id='test-key').decode()
        if state.get('tamper'):token=token[:-10]+'invalidxxx'
        return SimpleNamespace(status_code=200,json=lambda:{'id_token':token})
    monkeypatch.setattr(google.requests,'post',exchange)
    return app,client,database,state


def begin(site):
    app,client,database,state=site
    result=client.post('/cliente/google/iniciar',data={'csrf_token':csrf(client)})
    assert result.status_code==302
    params=parse_qs(urlsplit(result.location).query)
    assert params['scope']==['openid email'] and params['code_challenge_method']==['S256']
    assert params['redirect_uri']==[CONFIG['GOOGLE_LOGIN_REDIRECT_URI']]
    with sqlite3.connect(database) as db:
        nonce,verifier=db.execute('SELECT nonce,pkce_verifier FROM google_oauth_flows').fetchone()
    assert params['nonce']==[nonce]
    state.update(nonce=nonce,verifier_hash=hashlib.sha256(verifier.encode()).hexdigest())
    return params['state'][0]


def callback(site,**args):
    state=begin(site)
    return site[1].get('/cliente/google/retorno',query_string={'state':state,'code':'fake-authorization-code',**args},follow_redirects=True)


def test_first_link_requires_password_then_subject_login_and_no_staff_access(site):
    app,client,database,state=site
    result=callback(site)
    assert 'Confirmar vínculo' in result.text
    with client.session_transaction() as session:assert not session.get('client_id') and not session.get('staff')
    with sqlite3.connect(database) as db:assert db.execute('SELECT count(*) FROM google_client_identities').fetchone()[0]==0
    result=client.post('/cliente/google/confirmar',data={'password':'wrong','csrf_token':csrf(client)})
    assert result.status_code==400
    result=client.post('/cliente/google/confirmar',data={'password':'senha-segura','csrf_token':csrf(client)},follow_redirects=True)
    assert result.status_code==200
    with client.session_transaction() as session:
        assert session['client_id']==1 and not session.get('staff') and not session.get('staff_user_id') and not session.get('google_pending')
    assert client.get('/painel').status_code==302
    protected = client.post('/painel/configuracoes/pix',data={'csrf_token':csrf(client),'receiver':'Não salvar'})
    assert protected.status_code == 302 and '/login' in protected.location
    assert client.post('/cliente/sair',data={'csrf_token':csrf(client)}).status_code==302
    # The linked identity is recognized by subject, not remapped by a changed Google email.
    state['claims']={'email':'changed-google-email@example.com'}
    assert callback(site).status_code==200
    with client.session_transaction() as session:assert session['client_id']==1 and not session.get('staff')
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT count(*) FROM clients').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM google_client_identities').fetchone()[0]==1

@pytest.mark.parametrize('claim', [
    {'aud':'wrong-client'}, {'iss':'https://evil.invalid'}, {'email_verified':False}, {'email_verified':'true'},
    {'exp':1}, {'iat':int(time.time())+3600}, {'nonce':'wrong'}, {'azp':'wrong'}, {'sub':''},
])
def test_real_signed_tokens_rejected_for_invalid_claims(site,claim):
    site[3]['claims']=claim
    result=callback(site)
    assert 'Não foi possível validar' in result.text
    with site[1].session_transaction() as session:assert not session.get('client_id')


def test_forged_signature_and_google_unavailable(site,monkeypatch):
    site[3]['tamper']=True
    assert 'Não foi possível validar' in callback(site).text
    site[3]['tamper']=False
    monkeypatch.setattr(google.requests,'post',lambda *a,**k: (_ for _ in ()).throw(google.requests.Timeout('do not expose this secret')))
    result=callback(site)
    assert 'Não foi possível validar' in result.text and 'do not expose' not in result.text


def test_no_registration_no_automatic_account_and_message_visible(site):
    site[3]['claims']={'email':'new@example.com'}
    result=callback(site)
    assert google.NO_ACCOUNT in result.text and result.request.path=='/'
    assert 'id="cadastro"' in result.text
    with sqlite3.connect(site[2]) as db:assert db.execute('SELECT count(*) FROM clients').fetchone()[0]==1

def test_inactive_client_cannot_access(site):
    with sqlite3.connect(site[2]) as db:db.execute('UPDATE clients SET active=0')
    assert 'inativo' in callback(site).text
    with site[1].session_transaction() as session:assert not session.get('client_id')

@pytest.mark.parametrize('sql',[
    'UPDATE clients SET confirmation_validated_at=NULL',
    "UPDATE clients SET confirmation_channel='teste'",
    "UPDATE clients SET signature=''",
    "UPDATE clients SET residential_street=''",
])
def test_pending_client_authenticates_and_can_regularize(site,sql):
    with sqlite3.connect(site[2]) as db:db.execute(sql)
    assert 'Confirmar vínculo' in callback(site).text
    client=site[1]
    result=client.post('/cliente/google/confirmar',data={'password':'senha-segura','csrf_token':csrf(client)},follow_redirects=True)
    assert result.request.path=='/cliente'
    assert client.get('/cliente/pendencias').status_code==200
    assert client.get('/cliente/pendencias-e-autorizacoes').status_code==200
    assert client.get('/cliente/dados-de-entrega').status_code==403
    with client.session_transaction() as session:assert session['client_id']==1 and not session.get('staff')


def test_state_csrf_replay_cancellation_and_expiry(site):
    app,client,database,state=site
    assert client.post('/cliente/google/iniciar').status_code==400
    token=begin(site)
    cancelled=client.get('/cliente/google/retorno',query_string={'state':token,'error':'access_denied'},follow_redirects=True)
    assert cancelled.status_code==200 and 'cancelada' in cancelled.text
    assert state['exchange_count']==0
    assert client.get('/cliente/google/retorno',query_string={'state':token,'code':'replay'},follow_redirects=True).status_code==200
    assert state['exchange_count']==0
    token=begin(site)
    other=app.test_client()
    assert other.get('/cliente/google/retorno',query_string={'state':token,'code':'stolen'},follow_redirects=True).status_code==200
    assert state['exchange_count']==0
    with sqlite3.connect(database) as db:db.execute('UPDATE google_oauth_flows SET expires_at=0')
    assert 'expirou' in client.get('/cliente/google/retorno',query_string={'state':token,'code':'expired'},follow_redirects=True).text


def test_pending_rechecks_account_and_link_rate_limit(site):
    callback(site)
    client=site[1]
    for _ in range(5):
        client.post('/cliente/google/confirmar',data={'password':'wrong','csrf_token':csrf(client)})
    assert 'Muitas tentativas' in client.post('/cliente/google/confirmar',data={'password':'senha-segura','csrf_token':csrf(client)},follow_redirects=True).text
    with sqlite3.connect(site[2]) as db:assert db.execute('SELECT count(*) FROM google_client_identities').fetchone()[0]==0


def test_account_changes_duplicate_email_cancel_and_switch_identity(site):
    app,client,database,state=site
    callback(site)
    with sqlite3.connect(database) as db:db.execute('UPDATE clients SET session_version=session_version+1')
    assert 'cadastro mudou' in client.post('/cliente/google/confirmar',data={'password':'senha-segura','csrf_token':csrf(client)},follow_redirects=True).text
    callback(site)
    result=client.post('/cliente/google/confirmar',data={'action':'cancel','csrf_token':csrf(client)},follow_redirects=True)
    assert 'cancelada' in result.text
    callback(site)
    client.post('/cliente/google/confirmar',data={'password':'senha-segura','csrf_token':csrf(client)})
    client.post('/cliente/sair',data={'csrf_token':csrf(client)})
    state['claims']={'sub':'a-different-google-sub'}
    assert 'outra identidade' in callback(site).text


def test_config_hides_button_preserves_password_login_and_staff(site):
    app,client,database,state=site
    assert 'Fazer Login com o Google' in client.get('/cliente/login').text
    app.config['GOOGLE_LOGIN_ENABLED']='0'
    assert 'Fazer Login com o Google' not in client.get('/cliente/login').text
    assert client.post('/cliente/google/iniciar',data={'csrf_token':csrf(client)}).status_code==404
    assert client.post('/cliente/login',data={'cpf':'cliente@example.com','password':'senha-segura','csrf_token':csrf(client)}).location=='/cliente'
    client.post('/cliente/sair',data={'csrf_token':csrf(client)})
    app.config['GOOGLE_LOGIN_ENABLED']='1';app.config['GOOGLE_LOGIN_CLIENT_SECRET']=''
    assert 'Fazer Login com o Google' not in client.get('/cliente/login').text
    with client.session_transaction() as session:session.update(staff='staff',staff_legacy=True)
    assert client.post('/cliente/google/iniciar',data={'csrf_token':csrf(client)}).location=='/painel'


def test_duplicate_email_expired_pending_and_csrf_link_denied(site):
    app,client,database,state=site
    callback(site)
    assert client.post('/cliente/google/confirmar',data={'password':'senha-segura'}).status_code==400
    with sqlite3.connect(database) as db:db.execute('UPDATE google_login_pending SET expires_at=0')
    assert 'expirou' in client.get('/cliente/google/confirmar',follow_redirects=True).text
    with sqlite3.connect(database) as db:
        db.execute("""INSERT INTO clients(public_id,name,cpf,phone,email,terms_version,signature,accepted_at)
            VALUES('CLIPCA-2','Outro Cliente','98765432100','16999999999','cliente@example.com','v1','assinatura','2026-01-01')""")
    assert 'Mais de um cadastro' in callback(site).text
    with sqlite3.connect(database) as db:assert db.execute('SELECT count(*) FROM google_client_identities').fetchone()[0]==0


def test_invalid_state_does_not_destroy_legitimate_pending_authorization(site):
    token=begin(site)
    result=site[1].get('/cliente/google/retorno',query_string={'state':'invalid-state','code':'not-valid'},follow_redirects=True)
    assert 'Não foi possível validar' in result.text and site[3]['exchange_count']==0
    result=site[1].get('/cliente/google/retorno',query_string={'state':token,'code':'fake-authorization-code'},follow_redirects=True)
    assert 'Confirmar vínculo' in result.text and site[3]['exchange_count']==1


def test_google_session_uses_same_plan_eligibility_and_never_staff_permissions(site):
    from database import DatabaseConnection
    import plan_routes
    from datetime import datetime
    from core import TZ
    app,client,path,state=site
    assert 'Confirmar vínculo' in callback(site).text
    assert client.post('/cliente/google/confirmar',data={'password':'senha-segura','csrf_token':csrf(client)}).status_code==302
    assert client.get('/cliente/plano').status_code==200
    db=DatabaseConnection(path)
    owner=db.execute('SELECT * FROM clients WHERE id=1').fetchone()
    assert not plan_routes.eligible(db,owner,datetime.now(TZ))
    db.execute("UPDATE clients SET residential_street='' WHERE id=1");db.commit()
    owner=db.execute('SELECT * FROM clients WHERE id=1').fetchone()
    assert 'Rua residencial' in plan_routes.eligible(db,owner,datetime.now(TZ))
    assert client.get('/painel/configuracoes/plano').status_code==302
    db.close()
