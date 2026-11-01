"""Local-only OAuth browser fixtures: no real Google or email requests."""
import sys
import sqlite3
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'tests')]
from flask import request
import google_login
from app import create_app
from test_app import register

TEMP=tempfile.TemporaryDirectory(prefix='adelinha-google-')
DATABASE=str(Path(TEMP.name)/'test.db')
app=create_app({'TESTING':True,'SECRET_KEY':'isolated-google-browser', 'DATABASE':DATABASE,
    'GOOGLE_LOGIN_ENABLED':'1','GOOGLE_LOGIN_CLIENT_ID':'fixture-client',
    'GOOGLE_LOGIN_CLIENT_SECRET':'fixture-not-a-secret','GOOGLE_LOGIN_REDIRECT_URI':'http://127.0.0.1:5002/cliente/google/retorno'})

def identity(code,flow,config):
    if code=='failure':raise ValueError('Simulated provider unavailable')
    return 'fixture-sub-'+code, {'active':'cliente@example.com','inactive':'inactive@example.com','incomplete':'incomplete@example.com','new':'unregistered@example.com'}[code]
google_login.verified_identity=identity

@app.post('/__google/config')
def config():
    app.config['GOOGLE_LOGIN_ENABLED']=request.form.get('enabled','1')
    return {'ok':True}

@app.post('/__google/reset-link')
def reset():
    with sqlite3.connect(DATABASE) as db:db.execute('DELETE FROM google_client_identities')
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

if __name__=='__main__':app.run(host='127.0.0.1',port=5002)
