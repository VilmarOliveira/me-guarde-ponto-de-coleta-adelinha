"""One server-side, purpose-bound email challenge per client; shared by flows."""
import hashlib
import hmac
import re
import secrets
import time


def digest(secret,client_id,scope,code):
    return hmac.new(secret.encode(),f'{client_id}:{scope}:{code}'.encode(),hashlib.sha256).hexdigest()


def issue(db,secret,owner,scope,send_code):
    stamp=int(time.time())
    previous=db.execute('SELECT * FROM client_regularization_codes WHERE client_id=?',(owner['id'],)).fetchone()
    if previous and stamp-previous['sent_at']<60:return 429,'Aguarde 60 segundos antes de reenviar o código.'
    sends=previous['sends'] if previous and stamp-previous['window_start']<900 else 0
    if sends>=3:return 429,'Limite de envios atingido. Aguarde 15 minutos.'
    code=f'{secrets.randbelow(1000000):06d}';window=previous['window_start'] if sends else stamp
    reserved=db.execute('''INSERT INTO client_regularization_codes(client_id,scope_hash,code_hash,expires_at,attempts,sent_at,sends,window_start)
        VALUES(?,?,?,?,0,?,?,?) ON CONFLICT(client_id) DO UPDATE SET scope_hash=excluded.scope_hash,code_hash=excluded.code_hash,
        expires_at=excluded.expires_at,attempts=0,sent_at=excluded.sent_at,sends=excluded.sends,window_start=excluded.window_start
        WHERE client_regularization_codes.sent_at<=? AND (client_regularization_codes.sends<3 OR client_regularization_codes.window_start<=?) RETURNING client_id''',
        (owner['id'],scope,digest(secret,owner['id'],scope,code),stamp+600,stamp,sends+1,window,stamp-60,stamp-900)).fetchone();db.commit()
    if not reserved:return 429,'Aguarde antes de solicitar outro código.'
    try:sent,_=send_code('email',owner['email'],code)
    except Exception:sent=False
    if not sent:
        db.execute('UPDATE client_regularization_codes SET expires_at=0 WHERE client_id=? AND scope_hash=?',(owner['id'],scope));db.commit()
        return 503,'Envio de e-mail indisponível. Nenhum aceite ou confirmação foi registrado. Tente novamente mais tarde.'
    local,domain=owner['email'].split('@',1)
    return 200,f'Código enviado com sucesso para o e-mail {local[:2]}***@{domain}.'


def verify(db,secret,owner,scope,code):
    """Caller holds the write transaction and consumes the row on success."""
    row=db.execute('''UPDATE client_regularization_codes SET attempts=attempts+1 WHERE client_id=? AND scope_hash=?
        AND expires_at>? AND attempts<5 RETURNING *''',(owner['id'],scope,int(time.time()))).fetchone()
    return bool(row and re.fullmatch('[0-9]{6}',code) and hmac.compare_digest(row['code_hash'],digest(secret,owner['id'],scope,code)))
