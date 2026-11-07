"""Client-only OAuth authorization code + PKCE, OIDC and explicit first linking.
No Google client secret or ID/access token is stored in cookies, the database or logs.
"""
import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode, urlsplit

import requests
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from werkzeug.security import check_password_hash
from database import DatabaseIntegrityError
from core import valid_cpf
from client_compliance import diagnostics, compliance

NO_ACCOUNT = 'Para entrar com o Google, primeiro faça seu cadastro no site. Utilize o mesmo e-mail da sua conta Google.'
FAILURE = 'Não foi possível validar o acesso com Google. Tente novamente ou entre com CPF ou e-mail e senha.'


def configuration(config):
    enabled = str(config.get('GOOGLE_LOGIN_ENABLED', '')).lower() in ('1', 'true')
    client_id = config.get('GOOGLE_LOGIN_CLIENT_ID', '').strip()
    secret = config.get('GOOGLE_LOGIN_CLIENT_SECRET', '').strip()
    uri = config.get('GOOGLE_LOGIN_REDIRECT_URI', '').strip()
    try:
        parsed = urlsplit(uri)
    except ValueError:
        return None
    local_test = config.get('TESTING') and parsed.hostname in ('localhost', '127.0.0.1')
    valid = (parsed.scheme == 'https' or local_test and parsed.scheme == 'http') and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and parsed.path == '/cliente/google/retorno'
    return {'client_id': client_id, 'client_secret': secret, 'redirect_uri': uri} if enabled and client_id and secret and valid else None


class BoundedRequest(Request):
    def __call__(self, *args, **kwargs):
        kwargs['timeout'] = 10
        return super().__call__(*args, **kwargs)


def verified_identity(code, flow, config):
    response = requests.post('https://oauth2.googleapis.com/token', data={
        'grant_type': 'authorization_code', 'code': code,
        'client_id': config['client_id'], 'client_secret': config['client_secret'],
        'redirect_uri': config['redirect_uri'], 'code_verifier': flow['pkce_verifier'],
    }, timeout=10, allow_redirects=False)
    if response.status_code != 200:
        raise ValueError('token exchange failed')
    token = response.json().get('id_token')
    if not isinstance(token, str) or len(token) > 20000:
        raise ValueError('missing token')
    # Google-auth verifies signature using Google's certificates, exp, iat,
    # audience and issuer. No browser-provided profile is used for access.
    claims = id_token.verify_oauth2_token(token, BoundedRequest(), audience=config['client_id'])
    current = time.time()
    if (claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com')
        or claims.get('aud') != config['client_id']
        or claims.get('azp', config['client_id']) != config['client_id']
        or claims.get('email_verified') is not True
        or not isinstance(claims.get('nonce'), str)
        or not secrets.compare_digest(claims['nonce'], flow['nonce'])
        or not isinstance(claims.get('exp'), (int, float)) or claims['exp'] <= current
        or not isinstance(claims.get('iat'), (int, float)) or claims['iat'] > current + 60):
        raise ValueError('invalid identity claims')
    sub = claims.get('sub')
    email = claims.get('email')
    if not isinstance(sub, str) or not sub or len(sub) > 255 or not sub.isascii() or not isinstance(email, str) or len(email) > 254 or '@' not in email:
        raise ValueError('invalid identity')
    return sub, email.strip().lower()


def eligibility(client, record=None, email_confirmed=False):
    if not client['active']:
        return 'Este cadastro está inativo. Procure a equipe do ponto para regularizá-lo; o acesso não foi autorizado.'
    if diagnostics(client, record, email_confirmed):
        return 'Seu cadastro possui etapas pendentes. Entre com CPF ou e-mail e senha e consulte a lista em Área do Cliente → Pendências do cadastro para regularizar, sem refazer o cadastro.'
    return None


def install_google_login(app, db, login_blocked, record_login):
    def check_eligibility(client):
        # Authentication is independent of pending actions. An active client can
        # enter the center; dependent functions enforce their own restrictions.
        return None if client['active'] else eligibility(client)

    bp = Blueprint('google_login', __name__, url_prefix='/cliente/google')
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()

    def account():
        if session.get('staff'):
            return redirect(url_for('dashboard'))
        if session.get('client_id'):
            return redirect(url_for('client_area'))
        return None

    def fail(message=FAILURE):
        flash(message, 'error')
        return redirect(url_for('client_login'))

    def complete(client):
        destination=session.get("client_login_destination")
        session.clear()
        session['client_id'] = client['id']
        session['client_session_version'] = client['session_version']
        session['client_auth_method'] = 'google'
        return redirect(url_for('plans.client_hub' if destination=='plan' else 'client_area'))

    @app.context_processor
    def google_context():
        return {'google_login_enabled': bool(configuration(app.config)), 'google_registration_notice': NO_ACCOUNT if session.get('google_registration_required') else None}

    @bp.post('/iniciar')
    def start():
        existing = account()
        if existing is not None:
            return existing
        config = configuration(app.config)
        if not config: abort(404)
        session.pop('google_registration_required',None)
        stamp = int(time.time())
        for table in ('google_oauth_flows','google_login_pending'):
            db().execute(f'DELETE FROM {table} WHERE expires_at<=?', (stamp,))
        old = session.pop('google_flow', None)
        if old: db().execute('DELETE FROM google_oauth_flows WHERE state_hash=?', (digest(old),))
        pending = session.pop('google_pending', None)
        if pending: db().execute('DELETE FROM google_login_pending WHERE id_hash=?', (digest(pending),))
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        db().execute('INSERT INTO google_oauth_flows(state_hash,nonce,pkce_verifier,expires_at) VALUES(?,?,?,?)', (digest(state),nonce,verifier,stamp+600))
        db().commit()
        session['google_flow'] = state
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        return redirect('https://accounts.google.com/o/oauth2/v2/auth?' + urlencode({
            'client_id': config['client_id'], 'redirect_uri': config['redirect_uri'], 'response_type': 'code',
            'scope': 'openid email', 'state': state, 'nonce': nonce, 'code_challenge': challenge,
            'code_challenge_method': 'S256', 'prompt': 'select_account',
        }))

    @bp.get('/retorno')
    def callback():
        existing = account()
        if existing is not None:
            return existing
        config = configuration(app.config)
        state = session.get('google_flow', '')
        supplied = request.args.get('state','')
        if not config or not state or not supplied or not supplied.isascii() or not secrets.compare_digest(state, supplied):
            return fail()
        session.pop('google_flow',None)
        flow = db().execute('DELETE FROM google_oauth_flows WHERE state_hash=? RETURNING *', (digest(state),)).fetchone()
        db().commit()
        if not flow or flow['expires_at'] <= time.time(): return fail('A autenticação expirou. Inicie novamente o acesso com Google.')
        if request.args.get('error'):
            return fail('Autenticação com Google cancelada. Você pode tentar novamente ou usar sua senha.')
        code = request.args.get('code','')
        if not code or len(code)>4096: return fail()
        try:
            sub, email = verified_identity(code, flow, config)
        except Exception:
            # Errors may contain authorization codes or tokens: never log them.
            return fail()
        linked = db().execute('SELECT c.* FROM clients c JOIN google_client_identities g ON g.client_id=c.id WHERE g.google_sub=?', (sub,)).fetchone()
        if linked:
            problem = check_eligibility(linked)
            return fail(problem) if problem else complete(linked)
        matches = db().execute('SELECT * FROM clients WHERE LOWER(TRIM(email))=? LIMIT 2', (email,)).fetchall()
        if not matches:
            session['google_registration_required'] = True
            return redirect(url_for('registration_page'))
        if len(matches)!=1: return fail('Mais de um cadastro utiliza este e-mail. Procure a equipe para regularizar o cadastro antes de vincular o Google.')
        client = matches[0]
        problem = check_eligibility(client)
        if problem: return fail(problem)
        if db().execute('SELECT 1 FROM google_client_identities WHERE client_id=?', (client['id'],)).fetchone():
            return fail('Este cadastro já possui outra identidade Google vinculada. Use a conta vinculada ou entre com sua senha.')
        ticket = secrets.token_urlsafe(32)
        db().execute('INSERT INTO google_login_pending(id_hash,google_sub,email,client_id,session_version,expires_at) VALUES(?,?,?,?,?,?)',
                     (digest(ticket),sub,email,client['id'],client['session_version'],int(time.time())+600))
        db().commit()
        session['google_pending'] = ticket
        return redirect(url_for('google_login.confirm'))

    @bp.route('/confirmar', methods=['GET','POST'])
    def confirm():
        existing = account()
        if existing is not None:
            return existing
        if not configuration(app.config): return fail()
        ticket = session.get('google_pending','')
        pending = db().execute('SELECT * FROM google_login_pending WHERE id_hash=?', (digest(ticket),)).fetchone() if ticket else None
        if not pending or pending['expires_at']<=time.time():
            session.pop('google_pending',None)
            return fail('A confirmação expirou. Inicie novamente o acesso com Google.')
        if request.method=='POST' and request.form.get('action')=='cancel':
            db().execute('DELETE FROM google_login_pending WHERE id_hash=?',(digest(ticket),));db().commit()
            session.pop('google_pending',None)
            return fail('Vinculação cancelada. Você pode continuar entrando com sua senha.')
        client = db().execute('SELECT * FROM clients WHERE id=?',(pending['client_id'],)).fetchone()
        if not client: return fail()
        if not client['password_hash']:return fail('Recupere ou defina a senha do cadastro antes do primeiro vínculo com Google.')
        problem = check_eligibility(client)
        if problem: return fail(problem)
        if client['email'].strip().lower()!=pending['email'] or client['session_version']!=pending['session_version']: return fail('O cadastro mudou. Inicie novamente a vinculação.')
        identifier = str(client['id'])
        if login_blocked('google-link', identifier): return fail('Muitas tentativas. Aguarde 15 minutos e tente novamente.')
        if request.method=='POST':
            valid = check_password_hash(client['password_hash'],request.form.get('password',''))
            record_login('google-link',identifier,valid)
            if not valid:
                flash('Senha inválida. Use a senha do cadastro ou recupere-a antes de vincular.','error')
                return render_template('google_link.html'),400
            try:
                db().begin_write()
                # Recheck account status and consume the ticket in the same write transaction.
                fresh = db().execute('SELECT * FROM clients WHERE id=?',(client['id'],)).fetchone()
                consumed = db().execute('DELETE FROM google_login_pending WHERE id_hash=? AND expires_at>? RETURNING id_hash',(digest(ticket),int(time.time()))).fetchone()
                if not consumed or not fresh or check_eligibility(fresh) or fresh['session_version']!=pending['session_version'] or fresh['email'].strip().lower()!=pending['email']:
                    db().rollback(); return fail('A confirmação expirou ou o cadastro mudou. Inicie novamente.')
                db().execute('INSERT INTO google_client_identities(google_sub,client_id,email_at_link,linked_at) VALUES(?,?,?,?)', (pending['google_sub'],client['id'],pending['email'],int(time.time())))
                db().commit()
            except DatabaseIntegrityError:
                db().rollback(); return fail('Não foi possível vincular esta identidade. Use sua senha ou procure a equipe do ponto.')
            return complete(fresh)
        return render_template('google_link.html')

    app.register_blueprint(bp)
