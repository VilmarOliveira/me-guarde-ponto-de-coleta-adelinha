"""Local fixtures only: simulate provider errors and elapsed challenge time."""
import hashlib
import json
import sqlite3
from flask import request,session,abort
from serve_client_actions import app as seeded_app
from serve_google_login import DATABASE,CODES
from app import create_app
app=create_app(dict(seeded_app.config))
import app as application
original_send=application.send_code

def send(channel,email,code):
    if app.config.get('FIXTURE_EMAIL_FAIL'):return False,'simulated refusal'
    return original_send(channel,email,code)
application.send_code=send

@app.post('/__acceptance/challenge')
def challenge():
    cid=session.get('client_id')
    if not cid:abort(403)
    mode=request.form['mode']
    if mode in ('fail','restore'):app.config['FIXTURE_EMAIL_FAIL']=mode=='fail'
    with sqlite3.connect(DATABASE) as db:
        if mode=='ready':db.execute('UPDATE client_regularization_codes SET sent_at=0,window_start=0 WHERE client_id=?',(cid,))
        if mode=='expire':db.execute('UPDATE client_regularization_codes SET expires_at=0 WHERE client_id=?',(cid,))
    return {'ok':True}

@app.get('/__google/current-code')
def current_code():
    cid=session.get('client_id')
    if not cid:abort(403)
    with sqlite3.connect(DATABASE) as db:email=db.execute('SELECT email FROM clients WHERE id=?',(cid,)).fetchone()[0]
    return {'code':CODES.get(email)}

@app.get('/__acceptance/evidence')
def evidence():
    cid=session.get('client_id')
    if not cid:abort(403)
    with sqlite3.connect(DATABASE) as db:
        db.row_factory=sqlite3.Row
        root=dict(db.execute('SELECT * FROM clients WHERE id=?',(cid,)).fetchone())
        records=[dict(row) for row in db.execute('SELECT * FROM client_contract_records WHERE client_id=? ORDER BY id',(cid,))]
        responses=[dict(row) for row in db.execute('SELECT request_id,status FROM client_action_responses WHERE client_id=? ORDER BY id',(cid,))]
    return {'root_digest':hashlib.sha256(json.dumps(root,sort_keys=True).encode()).hexdigest(),'records':[{'id':row['id'],'source':row['source'],'digest':hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()} for row in records],'responses':responses}

if __name__=='__main__':app.run(host='127.0.0.1',port=5004)
