"""Subject separation, explicit acceptance and truthful delivery feedback."""
import sqlite3
import pytest
import app as application
from database import DatabaseConnection
from test_client_actions import center,site,signing_keys,rows,draft,publish,item,token,respond,ADMIN,HUB
from test_app import signature
from test_customization import csrf


def contract(center):
    rid=draft(center,is_contract='yes',require_existing='yes',document_version='ACEITE-ORGANIZADO',document_label='Contrato — Versão 1',body='Texto de uma revisão contratual específica.')
    publish(center,rid);return rid


def test_no_duplicate_ghost_resolution_and_ordered_contract_form(center):
    assert 'Nenhuma ação pendente no momento.' in center[2].get('/cliente').text
    rid=contract(center);client=center[2]
    page=client.get('/cliente').text
    assert 'class="button ghost" href="/cliente/pendencias-e-autorizacoes">Resolver pendência' not in page
    assert 'Pendências e autorizações (' in page and 'Há ações aguardando' in page
    form=client.get(f'{HUB}/{rid}').text
    assert 'Se faltam dados cadastrais, complete-os primeiro' not in form
    assert 'Conferir dados e regularização' not in form
    assert 'Concluir ação' not in form
    labels=['Assine abaixo','class="signature"','Limpar assinatura','Li e aceito os termos apresentados nesta versão.','Enviar código por e-mail','data-email-status','Código recebido','SALVAR DADOS']
    indices=[form.index(label) for label in labels];assert indices==sorted(indices)
    assert 'name="confirmed" value="yes" required>' in form # starts unchecked
    assert 'autocomplete="one-time-code"' in form


def test_provider_success_is_masked_and_only_authenticated_email_is_used(center,monkeypatch):
    rid=contract(center);client=center[2];scope=token(client,rid)
    response=client.post(f'{HUB}/{rid}',data={'email_intent':'send_code','choice':'accept','scope':scope,'email':'attacker@example.test','csrf_token':csrf(client)},headers={'X-Requested-With':'XMLHttpRequest'})
    assert response.status_code==200
    assert response.json=={'sent':True,'message':'Código enviado com sucesso para o e-mail cl***@example.com.'}
    assert center[4][0][0]=='cliente@example.com'
    assert rows(center,'client_action_responses')==[] and rows(center,'client_contract_records')==[]
    assert 'cliente@example.com' not in response.text
    with sqlite3.connect(center[3]) as db:db.execute('UPDATE client_regularization_codes SET sent_at=sent_at-61')
    assert client.post(f'{HUB}/{rid}',data={'choice':'send_code','scope':scope,'csrf_token':csrf(client)},headers={'X-Requested-With':'XMLHttpRequest'}).json['sent'] is True
    assert len(center[4])==2
    with sqlite3.connect(center[3]) as db:db.execute('UPDATE client_regularization_codes SET sent_at=sent_at-61')
    monkeypatch.setattr(application,'send_code',lambda *args:(False,'provider unavailable'))
    failed=client.post(f'{HUB}/{rid}',data={'choice':'send_code','scope':scope,'csrf_token':csrf(client)},headers={'X-Requested-With':'XMLHttpRequest'})
    assert failed.status_code==503 and failed.json['sent'] is False
    assert 'sucesso' not in failed.json['message']


def test_missing_profile_is_an_explicit_dependency_and_not_a_contract_form(center):
    rid=contract(center);initial_scope=token(center[2],rid)
    with sqlite3.connect(center[3]) as db:db.execute("UPDATE clients SET residential_street='' ")
    client=center[2];page=client.get(f'{HUB}/{rid}').text
    assert 'Resolva primeiro: Dados cadastrais' in page
    assert '/cliente/pendencias/dados' in page
    assert 'canvas' not in page and 'name="residential_street"' not in page
    assert 'Enviar código por e-mail' not in page
    assert client.post(f'{HUB}/{rid}',data={'choice':'send_code','scope':initial_scope,'csrf_token':csrf(client)}).status_code==400
    assert center[4]==[]
    dependency=draft(center,actions=['profile'],title='Confira seu endereço');publish(center,dependency)
    page=client.get(f'{HUB}/{rid}').text
    assert 'Resolva primeiro: Confira seu endereço' in page and f'href="{HUB}/{dependency}"' in page
    assert rows(center,'client_action_responses')==[]


def test_baseline_profile_and_contract_have_independent_routes_and_count(center):
    with sqlite3.connect(center[3]) as db:db.execute("UPDATE clients SET residential_street='',signature='' ")
    client=center[2];hub=client.get(HUB).text
    assert '<h2>Dados cadastrais</h2>' in hub and '<h2>Contrato</h2>' in hub
    assert 'Pendências e autorizações (2)' in hub
    profile=client.get('/cliente/pendencias/dados').text
    assert 'name="residential_street"' in profile and 'canvas.signature' not in profile and 'class="signature"' not in profile
    contract_page=client.get('/cliente/pendencias/contrato').text
    assert 'Resolva primeiro: Dados cadastrais' in contract_page and 'name="residential_street"' not in contract_page
    assert client.post('/cliente/pendencias/dados',data={'action':'confirm','csrf_token':csrf(client)}).status_code==400
    assert client.post('/cliente/pendencias/contrato',data={'action':'profile','csrf_token':csrf(client)}).status_code==400
    assert rows(center,'client_contract_records')==[]


def test_new_combined_requests_rejected_without_rewriting_existing_requests(center):
    admin=center[1]
    response=admin.post(ADMIN,data={'title':'Dois assuntos','explanation':'Separar cadastro e aceite','body':'Solicitação combinada','kind':'mandatory','audience':'all','actions':['profile','terms'],'csrf_token':csrf(admin)})
    assert response.status_code==400 and 'Separe os assuntos' in response.text
    assert rows(center,'client_action_requests')==[]


def test_acceptance_requires_checkbox_signature_code_and_completes_only_itself(center):
    rid=contract(center);other=draft(center,kind='optional',actions=['terms'],title='Autorização independente');publish(center,other)
    client=center[2];scope=token(client,rid);old=rows(center,'clients')[0].copy()
    client.post(f'{HUB}/{rid}',data={'choice':'send_code','scope':scope,'csrf_token':csrf(client)})
    code=center[4][-1][1]
    for fields in ({'signature':signature(),'code':code,'confirmed':''},{'code':code},{'signature':signature(),'code':'000bad'}):
        assert respond(center,rid,**fields).status_code==400
        assert rows(center,'client_action_responses')==[]
    with sqlite3.connect(center[3]) as db:db.execute('UPDATE client_regularization_codes SET expires_at=0')
    assert respond(center,rid,signature=signature(),code=code).status_code==400
    with sqlite3.connect(center[3]) as db:db.execute('UPDATE client_regularization_codes SET sent_at=sent_at-61')
    client.post(f'{HUB}/{rid}',data={'choice':'send_code','scope':scope,'csrf_token':csrf(client)})
    response=respond(center,rid,signature=signature(),code=center[4][-1][1])
    assert response.request.path==HUB and 'Dados atualizados com sucesso.' in response.text
    answers=rows(center,'client_action_responses');assert len(answers)==1 and answers[0]['request_id']==rid
    assert 'Autorização independente' in client.get('/cliente').text
    assert rows(center,'clients')[0]==old
    assert len(rows(center,'client_contract_records'))==2


def test_database_write_failure_rolls_back_contract_and_does_not_show_success(center):
    rid=contract(center);client=center[2];scope=token(client,rid)
    client.post(f'{HUB}/{rid}',data={'choice':'send_code','scope':scope,'csrf_token':csrf(client)})
    with sqlite3.connect(center[3]) as db:db.execute("CREATE TRIGGER fail_response BEFORE INSERT ON client_action_responses BEGIN SELECT RAISE(FAIL,'fixture-only write error'); END")
    response=respond(center,rid,signature=signature(),code=center[4][-1][1])
    assert response.status_code==503 and 'Não foi possível salvar' in response.text
    assert 'Dados atualizados com sucesso.' not in response.text
    assert rows(center,'client_action_responses')==[] and rows(center,'client_contract_records')==[]
    assert 'name="confirmed" value="yes" required checked' in response.text
    assert signature() in response.text
    with sqlite3.connect(center[3]) as db:db.execute('DROP TRIGGER fail_response')
    assert respond(center,rid,signature=signature(),code=center[4][-1][1]).status_code==200
