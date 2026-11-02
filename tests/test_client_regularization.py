import base64
import hashlib
import io
import re
import sqlite3

import pytest
from PIL import Image

import app as application
from app import create_app
from client_compliance import compliance, diagnostics, signature_data
from contract import CONTRACT_VERSION, CONTRACT_LABEL
from contract_v3 import CONTRACT_TEXT as PRIOR_TEXT
from test_google_login import site, signing_keys, callback
from test_app import signature
from test_customization import csrf

@pytest.fixture
def account(site,monkeypatch):
    app,client,database,state=site
    sent=[]
    monkeypatch.setattr(application,'send_code',lambda channel,destination,code:(sent.append((channel,destination,code)) or True,'test-only'))
    assert client.post('/cliente/login',data={'cpf':'52998224725','password':'senha-segura','csrf_token':csrf(client)}).location=='/cliente'
    return site,sent


def row(database):
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row
        return dict(db.execute('SELECT * FROM clients WHERE id=1').fetchone())


def mutate(database,sql,values=()):
    with sqlite3.connect(database) as db:db.execute(sql,values)


def scope(client):
    page=client.get('/cliente/pendencias')
    assert page.status_code==200
    return re.search(r'name="scope" value="([a-f0-9]+)"',page.text).group(1)


def send_and_confirm(account,new_signature=None):
    site,sent=account;client=site[1]
    value=scope(client)
    response=client.post('/cliente/pendencias',data={'action':'send_code','scope':value,'csrf_token':csrf(client)})
    assert response.status_code==200
    data={'action':'confirm','scope':value,'terms_accept':'yes','code':sent[-1][2],'csrf_token':csrf(client)}
    if new_signature:data['signature']=new_signature
    return client.post('/cliente/pendencias',data=data,follow_redirects=True)


def google_after_regularization(site):
    client=site[1]
    client.post('/cliente/sair',data={'csrf_token':csrf(client)})
    result=callback(site)
    assert 'Confirmar vínculo' in result.text
    assert client.post('/cliente/google/confirmar',data={'password':'senha-segura','csrf_token':csrf(client)}).location=='/cliente'


def test_complete_legacy_acceptance_without_control_metadata_is_valid(account):
    site,_=account;app,client,database,_=site
    mutate(database,'UPDATE clients SET contract_id=NULL,contract_hash=NULL,terms_version=?,terms_display_label=NULL',('CONTRATO-2026-v2',))
    before=row(database)
    response=client.get('/cliente/pendencias')
    assert 'completo e válido' in response.text
    assert 'Contrato — Versão 1' not in client.get('/cliente/condicoes-aceitas').text
    google_after_regularization(site)
    assert row(database)==before

@pytest.mark.parametrize('format,raw',[('PNG',False),('JPEG',False),('WEBP',False),('PNG',True)])
def test_legacy_signature_formats_not_rewritten(account,format,raw):
    site,_=account;client=site[1];database=site[2]
    stream=io.BytesIO();Image.new('RGB',(40,24),'black').save(stream,format)
    value=base64.b64encode(stream.getvalue()).decode()
    stored=value if raw else 'data:image/'+format.lower()+';base64,'+value
    mutate(database,'UPDATE clients SET signature=?',(stored,))
    assert 'completo e válido' in client.get('/cliente/pendencias').text
    google_after_regularization(site)
    assert row(database)['signature']==stored


def test_missing_email_proof_is_explicit_and_confirmed_without_new_signature(account):
    site,sent=account;client=site[1];database=site[2]
    mutate(database,'UPDATE clients SET confirmation_validated_at=NULL,contract_id=NULL,contract_hash=NULL')
    before=row(database)
    page=client.get('/cliente')
    assert 'Confirmação do contrato por código' in page.text
    assert 'Assinatura do contrato ausente' not in page.text
    result=send_and_confirm(account)
    assert 'Dados atualizados com sucesso.' in result.text
    assert result.request.path=='/cliente/pendencias-e-autorizacoes'
    assert row(database)==before
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT count(*) FROM client_contract_email_confirmations').fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM client_contract_records WHERE source='acceptance'").fetchone()[0]==0
    google_after_regularization(site)

@pytest.mark.parametrize('invalid', ['', 'data:image/png;base64,invalid', 'data:image/svg+xml;base64,PHN2Zy8+'])
def test_absent_or_invalid_signature_requires_explicit_new_acceptance(account,invalid):
    site,sent=account;client=site[1];database=site[2]
    mutate(database,'UPDATE clients SET signature=?',(invalid,))
    before=row(database)
    assert 'Assinatura' in client.get('/cliente').text
    result=send_and_confirm(account,signature())
    assert 'Dados atualizados com sucesso.' in result.text
    assert result.request.path=='/cliente/pendencias-e-autorizacoes'
    assert row(database)==before
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row
        records=db.execute('SELECT * FROM client_contract_records ORDER BY id').fetchall()
        assert len(records)==2 and records[0]['source']=='legacy' and records[0]['signature']==invalid
        assert records[1]['terms_version']==CONTRACT_VERSION and records[1]['terms_display_label']==CONTRACT_LABEL
        assert records[1]['signature']==signature() and records[1]['confirmation_channel']=='email'
    assert client.get('/cliente/contratos/1/pdf').status_code==200
    accepted=client.get('/cliente/condicoes-aceitas').text
    assert CONTRACT_LABEL in accepted and CONTRACT_VERSION in accepted
    assert client.get('/cliente/contrato-assinado.pdf').data.startswith(b'%PDF')
    google_after_regularization(site)


def test_missing_text_never_filled_from_current_version_automatically(account):
    site,_=account;client=site[1];database=site[2]
    mutate(database,"UPDATE clients SET terms_text=NULL,terms_version='legacy-v1',terms_display_label=NULL")
    result=send_and_confirm(account,signature())
    assert 'Dados atualizados com sucesso.' in result.text
    assert result.request.path=='/cliente/pendencias-e-autorizacoes'
    assert row(database)['terms_text'] is None and row(database)['terms_version']=='legacy-v1'
    assert 'legacy-v1' in client.get('/cliente/contratos').text


def test_profile_regularization_keeps_identifier_packages_and_original_contract(account):
    site,_=account;client=site[1];database=site[2]
    mutate(database,"UPDATE clients SET residential_street='' ")
    before=row(database)
    mutate(database,"INSERT INTO packages(public_id,client_id,tracking,width,height,length,weight,shelf,category,base_price,status,created_at) VALUES('PCT-KEEP',1,'TRACK-KEEP',10,10,10,1,'A1','Pequeno',4.9,'recebido','2026-01-01')")
    with sqlite3.connect(database) as db:original_package=db.execute('SELECT * FROM packages').fetchone()
    data={k:before[k] or '' for k in ('phone','email','residential_cep','residential_street','residential_number','residential_district','residential_city','residential_state','residential_complement')}
    response=client.post('/cliente/pendencias',data={**data,'action':'profile','residential_street':'Rua corrigida','name':'Nome forjado','cpf':'123','csrf_token':csrf(client)},follow_redirects=True)
    assert response.status_code==200 and 'dados cadastrais estão completos e válidos' in response.text
    after=row(database)
    for field in ('id','public_id','cpf','name','signature','terms_version','terms_text','accepted_at','contract_hash'):
        assert after[field]==before[field]
    assert after['residential_street']=='Rua corrigida'
    with sqlite3.connect(database) as db:assert db.execute('SELECT * FROM packages').fetchone()==original_package
    google_after_regularization(site)


def test_code_cannot_be_replayed_bruteforced_or_used_after_profile_changes(account):
    site,sent=account;client=site[1];database=site[2]
    mutate(database,'UPDATE clients SET confirmation_validated_at=NULL')
    value=scope(client)
    assert client.post('/cliente/pendencias',data={'action':'send_code','scope':value,'csrf_token':csrf(client)}).status_code==200
    assert client.post('/cliente/pendencias',data={'action':'send_code','scope':value,'csrf_token':csrf(client)}).status_code==429
    mutate(database,'UPDATE clients SET email=?',('changed@example.com',))
    assert client.post('/cliente/pendencias',data={'action':'confirm','scope':value,'terms_accept':'yes','code':sent[-1][2],'csrf_token':csrf(client)}).status_code==409
    mutate(database,'UPDATE clients SET email=?',('cliente@example.com',))
    for _ in range(5):
        assert client.post('/cliente/pendencias',data={'action':'confirm','scope':value,'terms_accept':'yes','code':'bad','csrf_token':csrf(client)}).status_code==400
    assert client.post('/cliente/pendencias',data={'action':'confirm','scope':value,'terms_accept':'yes','code':sent[-1][2],'csrf_token':csrf(client)}).status_code==400
    with sqlite3.connect(database) as db:assert db.execute('SELECT count(*) FROM client_contract_email_confirmations').fetchone()[0]==0


def test_email_failure_no_automatic_proof_and_inactive_accounts_blocked(account,monkeypatch):
    site,_=account;client=site[1];database=site[2]
    mutate(database,'UPDATE clients SET confirmation_validated_at=NULL')
    monkeypatch.setattr(application,'send_code',lambda *a:(False,'provider error'))
    assert client.post('/cliente/pendencias',data={'action':'send_code','scope':scope(client),'csrf_token':csrf(client)}).status_code==503
    with sqlite3.connect(database) as db:assert db.execute('SELECT count(*) FROM client_contract_email_confirmations').fetchone()[0]==0
    mutate(database,'UPDATE clients SET active=0')
    assert client.get('/cliente/pendencias').status_code==302


def test_only_owner_sees_issues_and_historical_documents(account):
    site,_=account;app,client,database,_=site
    mutate(database,'UPDATE clients SET confirmation_validated_at=NULL')
    send_and_confirm(account)
    anonymous=app.test_client()
    assert anonymous.get('/cliente/pendencias').status_code==302
    assert anonymous.get('/cliente/contratos/1/pdf').status_code==302
    mutate(database,"""INSERT INTO clients(public_id,name,cpf,phone,email,terms_version,signature,accepted_at)
        VALUES('CLIPCA-2','Outro Cliente','98765432100','16999999999','other@example.com','v1','signature','2026-01-01')""")
    with client.session_transaction() as session:session.update(client_id=2,client_session_version=1)
    assert client.get('/cliente/contratos/1/pdf').status_code==404
    assert 'cliente@example.com' not in client.get('/cliente/pendencias?client_id=1').text


def test_new_series_has_distinct_code_and_no_rewrite_of_old_acceptance(account):
    site,_=account;client=site[1];database=site[2]
    assert CONTRACT_VERSION not in ('v1','CONTRATO-2026-v2','CONTRATO-2026-v3')
    assert CONTRACT_LABEL=='Contrato — Versão 1'
    mutate(database,'UPDATE clients SET terms_version=?,terms_text=?,terms_display_label=NULL',('CONTRATO-2026-v3',PRIOR_TEXT))
    before=row(database)
    assert 'Contrato — Versão 1' not in client.get('/cliente/condicoes-aceitas').text
    assert 'CONTRATO-2026-v3' in client.get('/cliente/condicoes-aceitas').text
    assert CONTRACT_LABEL in client.get('/condicoes').text
    assert row(database)==before


def test_expired_email_code_and_missing_signature_never_create_acceptance(account):
    site,sent=account;client=site[1];database=site[2]
    mutate(database,"UPDATE clients SET signature='' ")
    value=scope(client)
    assert client.post('/cliente/pendencias',data={'action':'send_code','scope':value,'csrf_token':csrf(client)}).status_code==200
    data={'action':'confirm','scope':value,'terms_accept':'yes','code':sent[-1][2],'csrf_token':csrf(client)}
    assert client.post('/cliente/pendencias',data=data).status_code==400
    mutate(database,'UPDATE client_regularization_codes SET expires_at=0')
    assert client.post('/cliente/pendencias',data={**data,'signature':signature()}).status_code==400
    assert row(database)['signature']==''
    with sqlite3.connect(database) as db:assert db.execute("SELECT count(*) FROM client_contract_records WHERE source='acceptance'").fetchone()[0]==0


def test_profile_name_cpf_completion_archives_original_identification(account):
    site,_=account;client=site[1];database=site[2]
    mutate(database,"UPDATE clients SET name='',cpf='00000000000'")
    before=row(database)
    values={key:before[key] or '' for key in ('phone','email','residential_cep','residential_street','residential_number','residential_district','residential_city','residential_state','residential_complement')}
    response=client.post('/cliente/pendencias',data={**values,'action':'profile','name':'Titular identificado','cpf':'52998224725','csrf_token':csrf(client)},follow_redirects=True)
    assert response.status_code==200 and 'Identificação do titular no aceite' not in response.text
    assert 'Identificação do titular no aceite' in client.get('/cliente/pendencias/contrato').text
    with sqlite3.connect(database) as db:
        archived=db.execute("SELECT name,cpf,signature,terms_version FROM client_contract_records WHERE source='legacy'").fetchone()
    assert archived==('', '00000000000',before['signature'],before['terms_version'])
    assert row(database)['public_id']==before['public_id']
    assert 'Dados atualizados com sucesso.' in send_and_confirm(account,signature()).text
    google_after_regularization(site)


def test_signature_decoder_rejects_truncated_jpeg_even_with_real_header():
    stream=io.BytesIO();Image.new('RGB',(40,24),'black').save(stream,'JPEG')
    valid=stream.getvalue()
    assert signature_data('data:image/jpeg;base64,'+base64.b64encode(valid).decode())
    assert signature_data('data:image/jpeg;base64,'+base64.b64encode(valid[:-40]).decode()) is None
