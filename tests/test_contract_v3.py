import base64
import hashlib
import re
import sqlite3
import zlib

import app as application
from app import create_app, init_db, terms_text
from contract import CONTRACT_VERSION, CONTRACT_TEXT
from contract_v2 import CONTRACT_VERSION as OLD_VERSION, CONTRACT_TEXT as OLD_TEXT
from contract_pdf import build_contract_pdf
from test_app import register, signature
from test_customization import csrf


def pdf_text(content):
    streams=[]
    for match in re.findall(rb'stream\r?\n(.*?)endstream',content,re.S):
        try:streams.append(zlib.decompress(base64.a85decode(match.strip(),adobe=True)))
        except (ValueError,zlib.error):pass
    literals = re.findall(rb'\((.*?)\)\s*Tj', b'\n'.join(streams), re.S)
    return re.sub(rb'\s+', b' ', b' '.join(literals))


def test_v3_site_pdf_and_historical_snapshot_untouched(tmp_path,monkeypatch):
    database=str(tmp_path/'contracts.db')
    app=create_app({'TESTING':True,'SECRET_KEY':'test','DATABASE':database})
    client=app.test_client()
    with monkeypatch.context() as old:
        old.setattr(application,'TERMS_VERSION',OLD_VERSION)
        old.setattr(application,'CONTRACT_TEXT',OLD_TEXT)
        old.setattr(application,'CONTRACT_LABEL',None)
        assert register(client).status_code==200
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row
        before=dict(db.execute('SELECT * FROM clients WHERE id=1').fetchone())
    assert before['terms_version']==OLD_VERSION and '30 dias corridos' not in before['terms_text']
    init_db(database)
    with sqlite3.connect(database) as db:
        db.row_factory=sqlite3.Row
        assert dict(db.execute('SELECT * FROM clients WHERE id=1').fetchone())==before
    old_pdf=build_contract_pdf(before).getvalue()
    assert b'30 dias corridos' not in pdf_text(old_pdf)
    current=client.get('/condicoes')
    assert CONTRACT_VERSION in current.text and 'Versão v1' not in current.text
    assert current.text.count('INDENIZAÇÃO POR ENCOMENDAS SOB A GUARDA DO PONTO')==1
    assert 'não um prazo obrigatório estabelecido em lei' in current.text
    response=client.get('/condicoes/pdf')
    assert response.mimetype=='application/pdf' and response.data.startswith(b'%PDF')
    assert b'30 dias corridos' in pdf_text(response.data)
    with client.session_transaction() as session:session.update(client_id=1,client_session_version=1)
    assert OLD_VERSION in client.get('/cliente/condicoes-aceitas').text
    assert '30 dias corridos' not in client.get('/cliente/condicoes-aceitas').text


def test_new_acceptance_binds_displayed_version_and_confirmation(tmp_path,monkeypatch):
    database=str(tmp_path/'new-contract.db')
    app=create_app({'TESTING':True,'SECRET_KEY':'test','DATABASE':database})
    client=app.test_client()
    form={'name':'Cliente Completo','cpf':'52998224725','phone':'16999999999','email':'cliente@example.com',
          'signature':signature(),'password':'senha-segura','password_confirm':'senha-segura','terms_accept':'yes',
          'residential_cep':'14400000','residential_street':'Rua Teste','residential_number':'1','residential_district':'Centro',
          'residential_city':'Franca','residential_state':'SP','terms_version':CONTRACT_VERSION,
          'terms_digest':hashlib.sha256(terms_text().encode()).hexdigest(),'csrf_token':csrf(client)}
    captured={}
    monkeypatch.setattr(application,'send_code',lambda channel,email,code:(captured.update(code=code) or True,'test'))
    app.config['TESTING']=False
    assert client.post('/cadastro',data={**form,'terms_version':OLD_VERSION}).status_code==409
    assert client.post('/cadastro/enviar-codigo',data={**form,'terms_digest':'tampered'}).status_code==409
    assert client.post('/cadastro/enviar-codigo',data=form).status_code==200
    assert client.post('/cadastro',data=form).status_code==400  # no email code, no acceptance
    assert client.post('/cadastro',data={**form,'confirmation_code':captured['code']}).status_code==200
    with sqlite3.connect(database) as db:
        version,text,channel,confirmed=db.execute('SELECT terms_version,terms_text,confirmation_channel,confirmation_validated_at FROM clients').fetchone()
    assert version==CONTRACT_VERSION and text==terms_text() and channel=='email' and confirmed
