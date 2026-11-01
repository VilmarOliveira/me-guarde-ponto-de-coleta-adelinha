"""Local-only OAuth browser fixtures: no real Google or email requests."""
import sys
import sqlite3
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from flask import request, session, abort
import google_login
import app as application
from app import create_app
from test_app import register

TEMP=tempfile.TemporaryDirectory(prefix='adelinha-google-')
DATABASE=str(Path(TEMP.name)/'test.db')
app=create_app({'TESTING':True,'SECRET_KEY':'isolated-google-browser', 'DATABASE':DATABASE,
    'GOOGLE_LOGIN_ENABLED':'1','GOOGLE_LOGIN_CLIENT_ID':'fixture-client',
    'GOOGLE_LOGIN_CLIENT_SECRET':'fixture-not-a-secret','GOOGLE_LOGIN_REDIRECT_URI':'http://127.0.0.1:5002/cliente/google/retorno'})

def identity(code,flow,config):
    if code=='failure':raise ValueError('Simulated provider unavailable')
    return 'fixture-sub-'+code, {'active':'cliente@example.com','inactive':'inactive@example.com','incomplete':'incomplete@example.com','new':'unregistered@example.com','legacy':'legacy@example.com','profile':'profile@example.com','signature':'signature@example.com','confirmation':'confirmation@example.com'}[code]
google_login.verified_identity=identity

@app.post('/__google/config')
def config():
    app.config['GOOGLE_LOGIN_ENABLED']=request.form.get('enabled','1')
    return {'ok':True}

@app.post('/__google/reset-link')
def reset():
    with sqlite3.connect(DATABASE) as db:db.execute('DELETE FROM google_client_identities')
    return {'ok':True}

CODES={}
def send_code(channel,email,code):
    CODES[email]=code
    return True,'test-only'
application.send_code=send_code
ORIGINALS={}
@app.get('/__google/current-code')
def current_code():
    if not session.get('client_id'):abort(403)
    with sqlite3.connect(DATABASE) as db:
        email=db.execute('SELECT email FROM clients WHERE id=?',(session['client_id'],)).fetchone()[0]
    return {'code':CODES.get(email)}

@app.post('/__google/reset-compliance')
def reset_compliance():
    kind=request.form['scenario']
    if kind not in ORIGINALS:abort(400)
    original=ORIGINALS[kind]
    with sqlite3.connect(DATABASE) as db:
        for table in ('client_contract_email_confirmations','client_regularization_codes','client_contract_records','google_client_identities'):
            db.execute(f'DELETE FROM {table} WHERE client_id=?',(original['id'],))
        values={k:v for k,v in original.items() if k!='id'}
        db.execute('UPDATE clients SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),original['id']))
    return {'ok':True}

client=app.test_client()
assert register(client).status_code==200
with sqlite3.connect(DATABASE) as db:
    db.row_factory=sqlite3.Row
    db.execute("UPDATE clients SET confirmation_channel='email',confirmation_destination=email,confirmation_validated_at=accepted_at")
    original=dict(db.execute('SELECT * FROM clients').fetchone())
    for index,kind in [(2,'inactive'),(3,'incomplete')]:
        row={k:v for k,v in original.items() if k!='id'}
        row.update(public_id=f'CLIPCA-{index}',cpf='98765432100',email=kind+'@example.com',contract_id=f'CTRPCA-{index}')
        if kind=='inactive':row['active']=0
        else:row['confirmation_validated_at']=None
        cols=','.join(row); placeholders=','.join('?' for _ in row)
        db.execute(f'INSERT INTO clients({cols}) VALUES({placeholders})',tuple(row.values()))

with sqlite3.connect(DATABASE) as db:
    db.row_factory=sqlite3.Row
    original=dict(db.execute('SELECT * FROM clients WHERE id=1').fetchone())
    for index,kind in enumerate(('legacy','profile','signature','confirmation'),4):
        row={k:v for k,v in original.items() if k!='id'}
        digits=[int(c) for c in f'{index:09d}']
        for size in (10,11):digits.append((sum(a*b for a,b in zip(digits,range(size,1,-1)))*10%11)%10)
        row.update(public_id=f'CLIPCA-{index}',cpf=''.join(map(str,digits)),email=kind+'@example.com',contract_id=f'CTRPCA-{index}')
        if kind=='legacy':
            row.update(contract_id=None,contract_hash=None,terms_version='CONTRATO-2026-v2',terms_display_label=None)
            row['signature']=row['signature'].split(',',1)[1]
        if kind=='profile':row['residential_street']=''
        if kind=='signature':row['signature']=''
        if kind=='confirmation':row['confirmation_validated_at']=None
        db.execute('INSERT INTO clients('+','.join(row)+') VALUES('+','.join('?' for _ in row)+')',tuple(row.values()))
    for kind in ('active','legacy','profile','signature','confirmation','inactive'):
        email='cliente@example.com' if kind=='active' else kind+'@example.com'
        ORIGINALS[kind]=dict(db.execute('SELECT * FROM clients WHERE email=?',(email,)).fetchone())

if __name__=='__main__':app.run(host='127.0.0.1',port=5002)
