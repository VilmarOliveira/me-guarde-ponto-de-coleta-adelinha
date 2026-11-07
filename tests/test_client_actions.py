import hashlib
import json
import re
import sqlite3
from urllib.parse import urlsplit

import pytest
import app as application
from app import create_app,terms_text
from contract import CONTRACT_VERSION
from test_app import signature
from test_customization import csrf,legacy_login
from test_google_login import site,signing_keys,callback,begin

ADMIN='/painel/configuracoes/pendencias'
HUB='/cliente/pendencias-e-autorizacoes'

@pytest.fixture
def center(site,monkeypatch):
    app,client,database,state=site
    admin=app.test_client();legacy_login(admin)
    sent=[];monkeypatch.setattr(application,'send_code',lambda channel,email,code:(sent.append((email,code)) or True,'test-only'))
    client.post('/cliente/login',data={'cpf':'52998224725','password':'senha-segura','csrf_token':csrf(client)})
    return app,admin,client,database,sent,site


def rows(center,table):
    with sqlite3.connect(center[3]) as db:
        db.row_factory=sqlite3.Row
        return [dict(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY id')]


def draft(center,**changes):
    admin=center[1]
    data={'title':'Confirme uma atualização','explanation':'O que mudou e por que pedimos a ação','body':'Texto efetivamente apresentado ao cliente',
          'kind':'mandatory','audience':'all','actions':['read'],'functions':['delivery'],'starts_at':'2026-01-01T10:00',**changes,'csrf_token':csrf(admin)}
    response=admin.post(ADMIN,data=data)
    assert response.status_code==302,response.text
    return int(re.search(r'/pendencias/(\d+)',response.location).group(1))


def item(center,rid):return next(row for row in rows(center,'client_action_requests') if row['id']==rid)


def publish(center,rid):
    admin=center[1];row=item(center,rid)
    preview=admin.get(f'{ADMIN}/{rid}/previa');assert preview.status_code==200 and row['title'] in preview.text
    result=admin.post(f'{ADMIN}/{rid}/publicar',data={'content_hash':row['content_hash'],'confirm':'yes','csrf_token':csrf(admin)},follow_redirects=True)
    assert result.status_code==200,result.text


def token(client,rid):
    page=client.get(f'{HUB}/{rid}')
    assert page.status_code==200,page.text
    return re.search(r'name="scope" value="([a-f0-9]+)"',page.text).group(1)


def respond(center,rid,choice='accept',**extra):
    client=center[2]
    return client.post(f'{HUB}/{rid}',data={'scope':token(client,rid),'choice':choice,'confirmed':'yes','csrf_token':csrf(client),**extra},follow_redirects=True)


def test_draft_preview_no_automatic_answers_idempotent_publication_and_gates(center):
    app,admin,client,db,sent,_=center
    rid=draft(center)
    assert client.get(f'{HUB}/{rid}').status_code==404
    assert item(center,rid)['status']=='draft'
    assert admin.post(f'{ADMIN}/{rid}/publicar',data={'content_hash':item(center,rid)['content_hash'],'csrf_token':csrf(admin)}).status_code==409
    publish(center,rid);publish(center,rid)
    for _ in range(3):
        assert client.get('/cliente').status_code==200
        assert client.get(HUB).status_code==200
        assert client.get(f'{HUB}/{rid}').status_code==200
    assert len(rows(center,'client_action_assignments'))==1
    assert rows(center,'client_action_responses')==[]
    assert client.get('/cliente/dados-de-entrega').status_code==403
    for path in ('/cliente','/cliente/pendencias','/cliente/contratos','/cliente/contrato-assinado.pdf','/condicoes','/privacidade'):
        assert client.get(path).status_code==200
    scope=token(client,rid)
    assert client.post(f'{HUB}/{rid}',data={'scope':scope,'choice':'accept','csrf_token':csrf(client)}).status_code==400
    assert respond(center,rid).status_code==200
    assert client.get('/cliente/dados-de-entrega').status_code==200
    # Old form submitted twice must not duplicate evidence.
    client.post(f'{HUB}/{rid}',data={'scope':scope,'choice':'accept','confirmed':'yes','csrf_token':csrf(client)})
    assert len(rows(center,'client_action_responses'))==1
    answer=rows(center,'client_action_responses')[0]
    assert answer['body']==item(center,rid)['body'] and answer['request_version']==1 and answer['status']=='completed'


def test_optional_refusal_needs_no_signature_email_or_profile_and_never_blocks(center):
    rid=draft(center,kind='optional',actions=['terms','signature','email'],functions=['delivery','new_packages'])
    publish(center,rid)
    assert json.loads(item(center,rid)['functions_json'])==[]
    assert center[2].get('/cliente/dados-de-entrega').status_code==200
    assert respond(center,rid,'decline').status_code==200
    answer=rows(center,'client_action_responses')[0]
    assert answer['status']=='declined' and answer['signature'] is None and answer['email_confirmed_at'] is None
    assert center[4]==[]
    assert center[2].get('/cliente').status_code==200


def test_optional_accept_and_revoke_preserve_both_responses(center):
    rid=draft(center,kind='optional',actions=['terms'])
    publish(center,rid);assert respond(center,rid).status_code==200
    assert respond(center,rid,'revoke').status_code==200
    answers=rows(center,'client_action_responses')
    assert [x['status'] for x in answers]==['accepted','declined']
    assert [x['response'] for x in answers]==['accept','revoke']
    assert answers[0]['body']==answers[1]['body']==item(center,rid)['body']


def test_notice_never_blocks_even_after_deadline(center):
    rid=draft(center,kind='notice',functions=['delivery'],due_at='2026-01-02T10:00')
    publish(center,rid)
    assert center[2].get('/cliente/dados-de-entrega').status_code==200
    assert rows(center,'client_action_responses')==[]
    assert 'Prazo encerrado' in center[2].get(f'{HUB}/{rid}').text
    respond(center,rid)
    assert rows(center,'client_action_responses')[0]['status']=='completed'


def test_cancel_and_version_clone_never_change_old_responses(center):
    app,admin,client,_,_,_=center
    rid=draft(center);publish(center,rid);respond(center,rid)
    old_answer=rows(center,'client_action_responses')[0].copy();old_request=item(center,rid).copy()
    assert admin.post(f'{ADMIN}/{rid}',data={'title':'forged','csrf_token':csrf(admin)}).status_code==409
    result=admin.post(f'{ADMIN}/{rid}/nova-versao',data={'csrf_token':csrf(admin)})
    newer=int(result.location.rsplit('/',1)[1]);assert item(center,newer)['status']=='draft'
    assert admin.post(f'{ADMIN}/{newer}/publicar',data={'content_hash':item(center,newer)['content_hash'],'confirm':'yes','csrf_token':csrf(admin)}).status_code==302
    assert item(center,newer)['status']=='draft' # unchanged clone cannot create duplicate demand
    data={'title':'Nova atualização','explanation':'Mudança explícita','body':'Um novo texto de outra versão', 'kind':'mandatory','audience':'all','actions':['read'],'functions':['delivery'],'starts_at':'2026-01-01T10:00','csrf_token':csrf(admin)}
    assert admin.post(f'{ADMIN}/{newer}',data=data).status_code==302
    publish(center,newer)
    assert item(center,rid)['status']=='cancelled'
    assert rows(center,'client_action_responses')[0]==old_answer
    assert item(center,rid)['body']==old_request['body']
    assert center[2].get('/cliente/dados-de-entrega').status_code==403
    admin.post(f'{ADMIN}/{newer}/cancelar',data={'reason':'Não é mais necessária','csrf_token':csrf(admin)})
    assert center[2].get('/cliente/dados-de-entrega').status_code==200
    assert 'Cancelada' in center[2].get(HUB).text


def test_specific_audience_scheduling_and_client_isolation(center):
    app,admin,client,database,_,_=center
    rid=draft(center,audience='specific',client_codes='CLIPCA-1',starts_at='2099-01-01T00:00')
    publish(center,rid)
    assert client.get(f'{HUB}/{rid}').status_code==404
    rid2=draft(center,audience='specific',client_codes='CLIPCA-1');publish(center,rid2)
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO clients(public_id,name,cpf,phone,terms_version,signature,accepted_at) VALUES('CLIPCA-2','Outro Cliente','98765432100','16999999999','v1','', '2026-01-01')")
    outsider=app.test_client()
    with outsider.session_transaction() as sess:sess.update(client_id=2,client_session_version=1)
    assert outsider.get(f'{HUB}/{rid2}').status_code==404
    assert outsider.post(f'{HUB}/{rid2}',data={'choice':'accept','confirmed':'yes','scope':token(client,rid2),'csrf_token':csrf(outsider)}).status_code==404
    assert rows(center,'client_action_responses')==[]


def test_profile_action_reuses_edit_and_requires_real_save(center):
    rid=draft(center,actions=['profile'],audience='all');publish(center,rid)
    assert respond(center,rid).status_code==400
    with sqlite3.connect(center[3]) as db:
        db.row_factory=sqlite3.Row;person=dict(db.execute('SELECT * FROM clients WHERE id=1').fetchone())
    fields={k:person[k] or '' for k in ('phone','email','residential_cep','residential_street','residential_number','residential_district','residential_city','residential_state','residential_complement')}
    result=center[2].post('/cliente/editar',data={**fields,'csrf_token':csrf(center[2])})
    assert result.status_code==302
    assert respond(center,rid).status_code==200
    assert rows(center,'client_action_responses')[0]['profile_audit_id']


def test_email_signature_evidence_and_replay(center):
    rid=draft(center,actions=['terms','signature','email']);publish(center,rid)
    assert respond(center,rid).status_code==400
    client=center[2];value=token(client,rid)
    assert client.post(f'{HUB}/{rid}',data={'choice':'send_code','scope':value,'csrf_token':csrf(client)}).status_code==200
    assert respond(center,rid,signature=signature(),code='000000bad').status_code==400
    assert respond(center,rid,signature=signature(),code=center[4][-1][1]).status_code==200
    answer=rows(center,'client_action_responses')[0]
    assert answer['signature']==signature() and answer['email']=='cliente@example.com' and answer['email_confirmed_at']
    assert rows(center,'client_contract_records')==[] # generic terms are not fabricated contracts


def test_new_contract_publication_optional_existing_reaccept_and_append_history(center):
    app,admin,client,database,sent,_=center
    old=rows(center,'clients')[0].copy()
    rid=draft(center,is_contract='yes',require_existing='yes',document_version='CONTRATO-CENTRAL-2',document_label='Contrato publicado no painel',body='NOVO TEXTO CONTRATUAL, com aceite específico.',functions=['delivery','new_packages'])
    publish(center,rid)
    assert 'NOVO TEXTO CONTRATUAL' in client.get('/condicoes').text
    assert 'CONTRATO-CENTRAL-2' in app.test_client().get('/cadastro').text
    assert client.get('/cliente/dados-de-entrega').status_code==403
    assert client.get('/cliente/contrato-assinado.pdf').status_code==200
    value=token(client,rid);client.post(f'{HUB}/{rid}',data={'choice':'send_code','scope':value,'csrf_token':csrf(client)})
    assert respond(center,rid,signature=signature(),code=sent[-1][1]).status_code==200
    assert rows(center,'clients')[0]==old
    records=rows(center,'client_contract_records');assert len(records)==2
    assert records[-1]['terms_version']=='CONTRATO-CENTRAL-2' and records[-1]['terms_text']=='NOVO TEXTO CONTRATUAL, com aceite específico.'
    assert client.get('/cliente/dados-de-entrega').status_code==200
    assert 'CONTRATO-CENTRAL-2' in client.get('/cliente/condicoes-aceitas').text
    assert 'CONTRATO-CENTRAL-2' in client.get('/cliente?aba=contratos').text


def test_contract_without_reaccept_does_not_block_existing_clients(center):
    rid=draft(center,is_contract='yes',document_version='CONTRATO-CENTRAL-INFO',document_label='Novo contrato',body='Texto novo sem exigência retroativa',kind='notice',functions=['delivery'])
    publish(center,rid)
    assert item(center,rid)['kind']=='notice'
    assert center[2].get('/cliente/dados-de-entrega').status_code==200
    assert rows(center,'client_action_responses')==[]
    assert 'Texto novo sem exigência' in center[2].get('/condicoes').text


def test_contract_version_collision_rejected_and_old_preview_cannot_publish(center):
    admin=center[1]
    data={'title':'Contrato incorreto','explanation':'Não reutilizar versão para texto diferente','body':'outro texto','kind':'notice','audience':'all','actions':['read'],'is_contract':'yes','document_version':CONTRACT_VERSION,'csrf_token':csrf(admin)}
    assert admin.post(ADMIN,data=data).status_code==400
    rid=draft(center);old_hash=item(center,rid)['content_hash']
    assert admin.post(f'{ADMIN}/{rid}',data={'title':'Mudou','explanation':'Outra explicação','body':'Outro texto','kind':'notice','audience':'all','actions':['read'],'csrf_token':csrf(admin)}).status_code==302
    assert admin.post(f'{ADMIN}/{rid}/publicar',data={'content_hash':old_hash,'confirm':'yes','csrf_token':csrf(admin)}).status_code==409
    assert item(center,rid)['status']=='draft'


def test_administrator_cannot_answer_as_client_and_manager_cannot_manage(center):
    app,admin,client,database,_,_=center
    rid=draft(center);publish(center,rid)
    assert admin.post(f'{HUB}/{rid}',data={'choice':'accept','confirmed':'yes','csrf_token':csrf(admin)}).status_code==302
    assert rows(center,'client_action_responses')==[]
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO staff_users(id,name,email,password_hash,role,can_settings,created_at) VALUES(99,'Gerente','manager@example.test','hash','gerente',1,'2026-01-01')")
    manager=app.test_client()
    with manager.session_transaction() as sess:sess.update(staff='staff',staff_user_id=99,staff_session_version=1)
    assert manager.get(ADMIN).status_code==403
    assert manager.post(f'{ADMIN}/{rid}/cancelar',data={'csrf_token':csrf(manager)}).status_code==403
    assert client.get(ADMIN).status_code==302


def test_both_logins_authenticate_pending_clients_but_google_features_are_limited(center):
    app,admin,client,database,sent,site=center
    with sqlite3.connect(database) as db:db.execute("UPDATE clients SET signature='' ")
    client.post('/cliente/sair',data={'csrf_token':csrf(client)})
    result=callback(site)
    assert 'Confirmar vínculo' in result.text
    result=client.post('/cliente/google/confirmar',data={'password':'senha-segura','csrf_token':csrf(client)})
    assert result.location=='/cliente'
    assert client.get(HUB).status_code==200 and client.get('/cliente/pendencias').status_code==200
    assert client.get('/cliente/dados-de-entrega').status_code==403
    assert client.get('/cliente/contrato-assinado.pdf').status_code==200
    assert client.get('/painel').status_code==302


def test_published_current_contract_is_bound_to_new_registration_and_old_form_rejected(center):
    app,admin,client,_,sent,_=center
    old_version=CONTRACT_VERSION;old_digest=hashlib.sha256(terms_text().encode()).hexdigest()
    rid=draft(center,is_contract='yes',document_version='CONTRATO-NOVOS-3',document_label='Novo contrato para cadastro',body='Contrato para o cadastro novo, texto exato.',kind='notice')
    publish(center,rid)
    visitor=app.test_client();form={'name':'Novo Cliente Completo','cpf':'98765432100','phone':'16999999999','email':'novo@example.test',
        'signature':signature(),'password':'senha-segura','password_confirm':'senha-segura','terms_accept':'yes','residential_cep':'14400000',
        'residential_street':'Rua Teste','residential_number':'1','residential_district':'Centro','residential_city':'Franca','residential_state':'SP','csrf_token':csrf(visitor)}
    assert visitor.post('/cadastro',data={**form,'terms_version':old_version,'terms_digest':old_digest}).status_code==409
    form.update(terms_version='CONTRATO-NOVOS-3',terms_digest=hashlib.sha256('Contrato para o cadastro novo, texto exato.'.encode()).hexdigest())
    app.config['TESTING']=False
    assert visitor.post('/cadastro/enviar-codigo',data=form).status_code==200
    assert visitor.post('/cadastro',data={**form,'confirmation_code':sent[-1][1]}).status_code==200
    assert rows(center,'clients')[-1]['terms_version']=='CONTRATO-NOVOS-3'


def test_new_intake_gated_on_server_but_old_parcel_withdrawal_is_preserved(center):
    _,admin,client,_,_,_=center
    package={'client_id':1,'width':10,'height':10,'length':10,'weight':1,'shelf':'A1','tracking':'EXISTENTE','csrf_token':csrf(admin)}
    assert admin.post('/pacotes',data=package).status_code==302
    assert len(rows(center,'packages'))==1
    admin.post('/pacotes/1/avisar',data={'csrf_token':csrf(admin)})
    rid=draft(center,functions=['new_packages']);publish(center,rid)
    assert client.get('/cliente/dados-de-entrega').status_code==200
    denied=admin.post('/pacotes',data={**package,'tracking':'BLOQUEADO'},follow_redirects=True)
    assert 'Novas encomendas dependem de regularização' in denied.text
    assert len(rows(center,'packages'))==1
    pickup=admin.post('/retiradas',data={'package_ids':'1','receiver_name':'Cliente Teste','receiver_document':'52998224725','document_type':'CPF','payment_method':'Dinheiro','payment_confirmed':'yes','pickup_signature':signature(),'csrf_token':csrf(admin)})
    assert pickup.status_code==302
    assert rows(center,'packages')[0]['status']=='retirado'
    assert client.get('/cliente/comprovantes/1').status_code==200
    respond(center,rid)
    assert admin.post('/pacotes',data={**package,'tracking':'LIBERADO'}).status_code==302
    assert len(rows(center,'packages'))==2


def test_cancel_preserves_pending_audience_without_prior_client_visit(center):
    rid=draft(center);publish(center,rid)
    # Publication assigns existing clients; cancellation must not erase the audience.
    assert len(rows(center,'client_action_assignments'))==1
    center[1].post(f'{ADMIN}/{rid}/cancelar',data={'csrf_token':csrf(center[1])})
    assert 'Cancelada' in center[2].get(HUB).text
    assert rows(center,'client_action_responses')==[]


def test_persistence_and_staff_cannot_answer_even_with_mixed_session(center):
    rid=draft(center,kind='optional',actions=['terms']);publish(center,rid);respond(center,rid)
    snapshot=rows(center,'client_action_responses')
    app=create_app({**center[0].config,'DATABASE':center[3]})
    client=app.test_client()
    client.post('/cliente/login',data={'cpf':'cliente@example.com','password':'senha-segura','csrf_token':csrf(client)})
    assert 'Aceita' in client.get(HUB).text
    assert rows(center,'client_action_responses')==snapshot
    with client.session_transaction() as sess:sess.update(staff='staff',staff_legacy=True)
    assert client.post(f'{HUB}/{rid}',data={'scope':token(client,rid),'choice':'revoke','csrf_token':csrf(client)}).status_code==403
    assert rows(center,'client_action_responses')==snapshot


def test_code_failure_expiry_and_scope_do_not_create_answers(center,monkeypatch):
    rid=draft(center,actions=['email']);publish(center,rid)
    client=center[2];scope=token(client,rid)
    monkeypatch.setattr(application,'send_code',lambda *args:(False,'unavailable'))
    assert client.post(f'{HUB}/{rid}',data={'choice':'send_code','scope':scope,'csrf_token':csrf(client)}).status_code==503
    assert respond(center,rid,code='123456').status_code==400
    assert rows(center,'client_action_responses')==[]
    with sqlite3.connect(center[3]) as db:db.execute('DELETE FROM client_regularization_codes')
    monkeypatch.setattr(application,'send_code',lambda channel,email,code:(center[4].append((email,code)) or True,'test-only'))
    assert client.post(f'{HUB}/{rid}',data={'choice':'send_code','scope':scope,'csrf_token':csrf(client)}).status_code==200
    assert client.post(f'{HUB}/{rid}',data={'choice':'send_code','scope':scope,'csrf_token':csrf(client)}).status_code==429
    another=draft(center,actions=['email']);publish(center,another)
    assert respond(center,another,code=center[4][-1][1]).status_code==400
    with sqlite3.connect(center[3]) as db:db.execute('UPDATE client_regularization_codes SET expires_at=0')
    assert respond(center,rid,code=center[4][-1][1]).status_code==400
    assert rows(center,'client_action_responses')==[]


def test_concurrent_answers_do_not_duplicate_evidence(center):
    from concurrent.futures import ThreadPoolExecutor
    rid=draft(center,kind='optional',actions=['terms']);publish(center,rid)
    clients=[center[0].test_client(),center[0].test_client()]
    for client in clients:
        with client.session_transaction() as sess:sess.update(client_id=1,client_session_version=1)
    scopes=[token(client,rid) for client in clients]
    def submit(index):
        return clients[index].post(f'{HUB}/{rid}',data={'scope':scopes[index],'choice':'accept','confirmed':'yes','csrf_token':csrf(clients[index])}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(submit,range(2)))==[302,302]
    assert len(rows(center,'client_action_responses'))==1
