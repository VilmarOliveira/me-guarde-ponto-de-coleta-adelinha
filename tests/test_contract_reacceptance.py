"""Different revisions can share a visual label; consent never carries over."""
import re
import sqlite3
import pytest
from client_actions import accepted_contract,unpack,client_items
from client_compliance import revision_id,compliance
from database import DatabaseConnection
from app import create_app,terms_text
from contract import CONTRACT_VERSION,CONTRACT_LABEL
from test_app import signature
from test_customization import csrf
from test_client_actions import center,site,signing_keys,rows,draft,publish,item,token,respond,ADMIN,HUB
from test_google_login import callback
from scripts.require_current_contract_acceptance import run as rollout

SUMMARY='Novo compromisso de indenização e revisão das responsabilidades. Leia o texto integral antes de assinar.'

def old_contract(center):
    with sqlite3.connect(center[3]) as db:
        db.execute("UPDATE clients SET terms_version='CONTRATO-2026-v2',terms_text='Texto antigo contratado',terms_display_label=?,contract_revision_id=NULL",(CONTRACT_LABEL,))
    return rows(center,'clients')[0].copy()


def prepare(center,summary=SUMMARY,revision=None):
    return center[1].post(ADMIN+'/contrato-vigente/exigir-aceite',data={'summary':summary,'require_existing':'yes',
        'expected_revision':revision or revision_id(CONTRACT_VERSION,terms_text()),'csrf_token':csrf(center[1])})


def test_old_signed_contract_gets_current_revision_pending_and_full_evidence_is_required(center):
    app,admin,client,database,sent,site=center
    old=old_contract(center)
    db=DatabaseConnection(database)
    assert compliance(db,db.execute('SELECT * FROM clients WHERE id=1').fetchone())[1]==[]
    db.close()
    assert rows(center,'client_action_requests')==[]
    assert '1</b> cliente(s) ativo(s)' in admin.get(ADMIN).text
    created=prepare(center);rid=int(re.search(r'/pendencias/(\d+)',created.location).group(1))
    assert item(center,rid)['status']=='draft'
    assert item(center,rid)['body']==terms_text() and item(center,rid)['require_existing']==1
    assert client.get(f'{HUB}/{rid}').status_code==404
    publish(center,rid)
    assert 'Novo aceite do contrato vigente' in client.get('/cliente').text
    assert SUMMARY in client.get('/cliente').text
    assert item(center,rid)['document_label']==old['terms_display_label']==CONTRACT_LABEL
    # Password and Google both show the same pending request, without a new account.
    client.post('/cliente/sair',data={'csrf_token':csrf(client)})
    assert 'Confirmar vínculo' in callback(site).text
    assert client.post('/cliente/google/confirmar',data={'password':'senha-segura','csrf_token':csrf(client)}).location=='/cliente'
    assert 'Novo aceite do contrato vigente' in client.get('/cliente').text
    page=client.get(f'{HUB}/{rid}');assert terms_text().splitlines()[0] in page.text
    assert revision_id(CONTRACT_VERSION,terms_text()) in page.text
    assert respond(center,rid,signature=signature()).status_code==400
    assert rows(center,'client_action_responses')==[]
    scope=token(client,rid)
    client.post(f'{HUB}/{rid}',data={'scope':scope,'choice':'send_code','csrf_token':csrf(client)})
    assert respond(center,rid,code=sent[-1][1]).status_code==400
    assert rows(center,'client_action_responses')==[]
    assert respond(center,rid,signature=signature(),code=sent[-1][1]).status_code==200
    assert rows(center,'clients')[0]==old
    records=rows(center,'client_contract_records')
    assert records[0]['terms_text']==old['terms_text'] and records[0]['signature']==old['signature'] and records[0]['accepted_at']==old['accepted_at']
    assert records[-1]['contract_revision_id']==revision_id(CONTRACT_VERSION,terms_text())
    assert records[-1]['confirmation_validated_at'] and records[-1]['signature'] and records[-1]['contract_id']
    db=DatabaseConnection(database);person=db.execute('SELECT * FROM clients WHERE id=1').fetchone()
    assert not client_items(db,person,'2099-01-01T00:00:00-03:00');db.close()
    assert 'Seu histórico nesta solicitação' in client.get(f'{HUB}/{rid}').text
    assert client.get('/cliente/contratos').status_code==200
    assert len(rows(center,'clients'))==1


def test_existing_informative_publication_can_require_acceptance_without_mutation(center):
    old_contract(center)
    rid=draft(center,kind='notice',is_contract='yes',document_version='REVISAO-JA-PUBLICADA',document_label=CONTRACT_LABEL,body='Contrato novo já publicado, diferente do antigo.')
    publish(center,rid);respond(center,rid)
    original=item(center,rid).copy();old_answer=rows(center,'client_action_responses')[0].copy()
    revision=revision_id(original['document_version'],original['body'])
    result=prepare(center,revision=revision);newer=int(re.search(r'/pendencias/(\d+)',result.location).group(1))
    assert newer!=rid and item(center,newer)['version']==2
    assert item(center,newer)['document_version']==original['document_version'] and item(center,newer)['body']==original['body']
    publish(center,newer)
    assert item(center,rid)['kind']=='notice' and item(center,rid)['require_existing']==0
    assert rows(center,'client_action_responses')[0]==old_answer
    assert item(center,rid)['status']=='cancelled'
    assert client_has_pending(center,newer)
    assert prepare(center,revision=revision).location.endswith(f'/{newer}/acompanhamento')
    assert len(rows(center,'client_action_requests'))==2
    assert rows(center,'clients')[0]['terms_version']=='CONTRATO-2026-v2'


def client_has_pending(center,rid):
    db=DatabaseConnection(center[3]);person=db.execute('SELECT * FROM clients WHERE id=1').fetchone()
    result=any(req['id']==rid for req in client_items(db,person,'2099-01-01T00:00:00-03:00'));db.close();return result


def test_label_is_not_document_identity_and_legacy_same_exact_revision_remains_valid(center):
    db=DatabaseConnection(center[3]);person=db.execute('SELECT * FROM clients WHERE id=1').fetchone()
    assert accepted_contract(db,person,CONTRACT_VERSION,terms_text())
    assert not accepted_contract(db,person,'OUTRO-CODIGO',terms_text())
    assert not accepted_contract(db,person,CONTRACT_VERSION,terms_text()+'\nOutro texto')
    db.execute('UPDATE clients SET contract_revision_id=NULL');db.commit()
    person=db.execute('SELECT * FROM clients WHERE id=1').fetchone()
    assert accepted_contract(db,person,CONTRACT_VERSION,terms_text()) # old real evidence, not a fabricated confirmation
    db.execute("UPDATE clients SET contract_revision_id='REV-errada'");db.commit()
    person=db.execute('SELECT * FROM clients WHERE id=1').fetchone()
    assert not accepted_contract(db,person,CONTRACT_VERSION,terms_text())
    db.close()


def test_same_revision_customer_not_asked_again_and_preparation_is_idempotent(center):
    first=prepare(center);second=prepare(center)
    assert first.location==second.location
    rid=rows(center,'client_action_requests')[0]['id'];publish(center,rid)
    assert not client_has_pending(center,rid)
    assert rows(center,'client_action_assignments')==[]
    assert rows(center,'client_action_responses')==[]
    assert prepare(center).location.endswith(f'/{rid}/acompanhamento')


def test_preparation_rejects_stale_revision_and_missing_admin_confirmation(center):
    admin=center[1]
    assert admin.post(ADMIN+'/contrato-vigente/exigir-aceite',data={'summary':SUMMARY,'csrf_token':csrf(admin)}).status_code==400
    response=prepare(center,revision='REV-stale');assert response.status_code==302
    assert rows(center,'client_action_requests')==[]
    assert prepare(center,summary='').status_code==302
    assert rows(center,'client_action_requests')==[]
    assert center[2].post(ADMIN+'/contrato-vigente/exigir-aceite',data={'summary':SUMMARY,'csrf_token':csrf(center[2])}).status_code==302
    assert rows(center,'client_action_requests')==[]


def test_response_status_alone_does_not_complete_new_contract(center):
    old_contract(center);created=prepare(center);rid=int(re.search(r'/pendencias/(\d+)',created.location).group(1));publish(center,rid)
    with sqlite3.connect(center[3]) as db:
        # Simulate an incomplete record from an earlier integration. Never accept
        # its status flag as a replacement for the contractual evidence.
        r=item(center,rid)
        db.execute('''INSERT INTO client_action_responses(request_id,client_id,request_version,title,explanation,body,content_hash,actions_json,response,status,responded_at)
            VALUES(?,1,1,?,?,?,?,?,'accept','accepted','2026-01-01')''',(rid,r['title'],r['explanation'],r['body'],r['content_hash'],r['actions_json']))
    assert client_has_pending(center,rid)
    assert respond(center,rid,signature=signature()).status_code==400
    client=center[2];scope=token(client,rid)
    client.post(f'{HUB}/{rid}',data={'scope':scope,'choice':'send_code','csrf_token':csrf(client)})
    assert respond(center,rid,signature=signature(),code=center[4][-1][1]).status_code==200
    assert not client_has_pending(center,rid)
    assert len(rows(center,'client_action_responses'))==2


def test_rollout_is_read_only_by_default_and_requires_reviewed_hash(center,capsys):
    old=old_contract(center);revision=revision_id(CONTRACT_VERSION,terms_text())
    rollout(center[3]);assert rows(center,'client_action_requests')==[]
    assert 'Clientes ativos sem aceite válido dessa revisão: 1' in capsys.readouterr().out
    with pytest.raises(ValueError):rollout(center[3],SUMMARY,publish=True,expected_revision=revision)
    assert rows(center,'client_action_requests')==[]
    rollout(center[3],SUMMARY,prepare=True,expected_revision=revision)
    row=rows(center,'client_action_requests')[0]
    with pytest.raises(RuntimeError):rollout(center[3],SUMMARY,publish=True,expected_revision=revision,preview_hash='stale')
    assert item(center,row['id'])['status']=='draft'
    rollout(center[3],SUMMARY,publish=True,expected_revision=revision,preview_hash=row['content_hash'])
    assert client_has_pending(center,row['id'])
    rollout(center[3],SUMMARY,publish=True,expected_revision=revision,preview_hash=row['content_hash'])
    assert len(rows(center,'client_action_requests'))==1
    assert rows(center,'clients')[0]==old and rows(center,'client_action_responses')==[]
    assert 'cliente@example.com' not in capsys.readouterr().out


def test_informative_read_never_creates_contract_or_email_evidence(center):
    old=old_contract(center)
    rid=draft(center,kind='notice',is_contract='yes',document_version='SOMENTE-AVISO',document_label=CONTRACT_LABEL,body='Texto vigente informativo.')
    publish(center,rid);assert respond(center,rid).status_code==200
    assert rows(center,'client_contract_records')==[]
    assert rows(center,'client_contract_email_confirmations')==[]
    answer=rows(center,'client_action_responses')[0]
    assert answer['contract_record_id'] is None and answer['email_confirmed_at'] is None and answer['signature'] is None
    assert rows(center,'clients')[0]==old


def test_historical_notice_attachment_is_preserved_but_not_used_as_signed_contract(center):
    from client_compliance import FIELDS,effective_record
    old=old_contract(center)
    rid=draft(center,kind='notice',is_contract='yes',document_version='AVISO-ANTIGO',body='Texto que não foi assinado.')
    publish(center,rid);respond(center,rid)
    db=DatabaseConnection(center[3]);fields={name:old.get(name) for name in FIELDS}
    fields.update(terms_version='AVISO-ANTIGO',terms_text='Texto que não foi assinado.',signature=None,contract_revision_id=None)
    fake=db.execute('INSERT INTO client_contract_records(client_id,source,recorded_at,'+','.join(FIELDS)+') VALUES(?,?,?,'+','.join('?' for _ in FIELDS)+') RETURNING id',(1,'acceptance','2026-01-01',*[fields[k] for k in FIELDS])).fetchone()['id']
    db.execute('UPDATE client_action_responses SET contract_record_id=? WHERE request_id=?',(fake,rid));db.commit()
    before=dict(db.execute('SELECT * FROM client_contract_records WHERE id=?',(fake,)).fetchone())
    selected=effective_record(db,db.execute('SELECT * FROM clients WHERE id=1').fetchone())
    assert selected['terms_text']==old['terms_text'] and selected['signature']==old['signature']
    assert dict(db.execute('SELECT * FROM client_contract_records WHERE id=?',(fake,)).fetchone())==before
    db.close()
    assert 'Texto antigo contratado' in center[2].get('/cliente/condicoes-aceitas').text


def test_upgrade_adds_revision_field_without_rewriting_old_evidence(center):
    old=old_contract(center);old.pop('contract_revision_id')
    with sqlite3.connect(center[3]) as db:
        db.execute('ALTER TABLE clients DROP COLUMN contract_revision_id')
        db.execute('ALTER TABLE client_contract_records DROP COLUMN contract_revision_id')
    create_app({**center[0].config,'DATABASE':center[3]})
    upgraded=rows(center,'clients')[0]
    assert upgraded.pop('contract_revision_id') is None and upgraded==old
    assert rows(center,'client_contract_records')==[] and rows(center,'client_action_requests')==[]


def test_readonly_report_does_not_create_empty_database(tmp_path):
    missing=tmp_path/'not-a-deployment.db'
    with pytest.raises(ValueError):rollout(str(missing))
    assert not missing.exists()
