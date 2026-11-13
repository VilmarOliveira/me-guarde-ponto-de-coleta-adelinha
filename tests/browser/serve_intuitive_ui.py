"""Private visual fixture with temporary data and fake providers only."""
import sys
import tempfile
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from flask import session, redirect, abort, url_for
from werkzeug.security import generate_password_hash
import app as application
import plan_routes
from app import create_app
from database import DatabaseConnection
from test_app import register
from test_plan import CONFIG, FakeFlow, post_purchase
from test_customer_payments import package

TEMP = tempfile.TemporaryDirectory(prefix='intuitive-ui-')
application.send_code = lambda *a, **k: (False,'disabled in visual fixture')
application.gmail_authorization_status = lambda: (True,'Autorização simulada; nenhum e-mail enviado')
app = create_app({**CONFIG,'SECRET_KEY':'visual-fixture-only','DATABASE':str(Path(TEMP.name)/'visual.db'),'STAFF_USER':'fixture','STAFF_PASSWORD_HASH':generate_password_hash('visual-fixture-only')})
fake = FakeFlow()
plan_routes.gateway = lambda app: fake

@app.get('/__visual__/public')
def public():
    session.clear()
    return redirect(url_for('index'))

@app.get('/__visual__/customer/<tab>')
def customer(tab):
    if tab not in ('visao-geral','pacotes','dados','historico','plano','contratos'): abort(404)
    session.clear();session.update(client_id=1,client_session_version=1)
    return redirect(url_for('client_area',aba=tab))

@app.get('/__visual__/staff')
def staff():
    session.clear();session.update(staff='fixture',csrf_token='visual-fixture-only')
    return redirect(url_for('dashboard'))

client = app.test_client()
register(client)
db = DatabaseConnection(app.config['DATABASE'])
db.execute('UPDATE clients SET confirmation_channel=\'email\',confirmation_destination=email,confirmation_validated_at=accepted_at')
db.execute("INSERT INTO google_client_identities(google_sub,client_id,email_at_link,linked_at) VALUES('visual-only',1,'example@example.com',?)",(int(time.time()),))
db.commit();package(db);db.close()
post_purchase(client)
if __name__ == '__main__':
    app.run(host='127.0.0.1',port=5095)
