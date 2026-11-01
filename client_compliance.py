"""Shared authenticated compliance diagnostics; immutable acceptance snapshots."""
import base64
import hashlib
import json
import re
from datetime import datetime
from io import BytesIO
from PIL import Image, UnidentifiedImageError
from core import valid_cpf

FIELDS = ('public_id','name','cpf','terms_version','terms_text','signature','accepted_at','contract_id','contract_hash',
          'contract_public_id','document_brand_version','document_logo_present','confirmation_channel',
          'confirmation_destination','confirmation_validated_at','accept_ip','accept_user_agent','terms_display_label')


def signature_data(value):
    """Accept actual legacy raster formats without changing stored evidence."""
    if not isinstance(value,str) or not value or len(value)>3_000_000: return None
    payload=value.split(',',1)[1] if value.lstrip().lower().startswith('data:image/') and ',' in value else value
    try:
        data=base64.b64decode(re.sub(r'\s+','',payload),validate=True)
        if len(data)>2_000_000:return None
        with Image.open(BytesIO(data)) as im:
            fmt=im.format
            if fmt not in ('PNG','JPEG','WEBP') or im.width*im.height>16_000_000:return None
            im.verify()
        with Image.open(BytesIO(data)) as decoded:
            decoded.load()
        mime={'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}[fmt]
        return 'data:'+mime+';base64,'+base64.b64encode(data).decode()
    except (ValueError,OSError,UnidentifiedImageError,Image.DecompressionBombError):return None


def valid_date(value):
    try:return bool(value and datetime.fromisoformat(value.replace('Z','+00:00')))
    except (ValueError,TypeError,AttributeError):return False


def valid_email(value):
    return bool(isinstance(value,str) and len(value)<=254 and re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value))


def snapshot(client):
    data=dict(client)
    return {key:data.get(key) for key in FIELDS}


def acceptance_key(record):
    values={k:record.get(k) for k in ('public_id','name','cpf','terms_version','terms_text','signature','accepted_at')}
    values['public_id']=record.get('contract_public_id') or record.get('public_id')
    return hashlib.sha256(json.dumps(values,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def records(db,client):
    return [dict(r) for r in db.execute('SELECT * FROM client_contract_records WHERE client_id=? ORDER BY id DESC',(client['id'],)).fetchall()]


def effective_record(db,client):
    rows=records(db,client)
    return next((r for r in rows if r['source']=='acceptance'),None) or next((r for r in rows if r['source']=='legacy'),None) or snapshot(client)


def archive_legacy(db,client,stamp):
    values=snapshot(client)
    columns=','.join(FIELDS)
    placeholders=','.join('?' for _ in FIELDS)
    db.execute(f'INSERT INTO client_contract_records(client_id,source,recorded_at,{columns}) VALUES(?,?,?,{placeholders}) ON CONFLICT DO NOTHING',
               (client['id'],'legacy',stamp,*values.values()))


def diagnostics(client,record=None,email_confirmed=False):
    """Return machine keys and safe labels; render only after client login."""
    client=dict(client);record=dict(record or client)
    missing=[]
    checks=[('name','Nome completo',len((client.get('name') or '').strip())>=5),
            ('cpf','CPF válido',valid_cpf(client.get('cpf') or '')),
            ('phone','WhatsApp',len(re.sub(r'\D','',client.get('phone') or ''))>=10),
            ('email','E-mail válido',valid_email(client.get('email'))),
            ('password','Senha de acesso',bool(client.get('password_hash')))]
    for key,label,ok in checks:
        if not ok:missing.append({'key':key,'label':label,'group':'profile'})
    address={'residential_cep':'CEP residencial','residential_street':'Rua residencial','residential_number':'Número residencial',
             'residential_district':'Bairro residencial','residential_city':'Cidade residencial','residential_state':'Estado residencial (UF)'}
    for key,label in address.items():
        value=(client.get(key) or '').strip()
        ok=bool(value)
        if key=='residential_cep':ok=len(re.sub(r'\D','',value))==8
        if key=='residential_state':ok=bool(re.fullmatch('[A-Za-z]{2}',value))
        if not ok:missing.append({'key':key,'label':label,'group':'profile'})
    if len((record.get('name') or '').strip())<5 or not valid_cpf(record.get('cpf') or ''):
        missing.append({'key':'contract_identity','label':'Identificação do titular no aceite','group':'acceptance'})
    for key,label,ok in [('signature','Assinatura do contrato ausente' if not record.get('signature') else 'Assinatura em formato não reconhecido ou arquivo ilegível',bool(signature_data(record.get('signature')))),
                         ('terms_version','Identificação da versão aceita',bool(record.get('terms_version'))),
                         ('terms_text','Texto do contrato aceito disponível',bool(record.get('terms_text'))),
                         ('accepted_at','Data do aceite do contrato',valid_date(record.get('accepted_at')))]:
        if not ok:missing.append({'key':key,'label':label,'group':'acceptance'})
    historical_email=(record.get('confirmation_channel')=='email' and valid_date(record.get('confirmation_validated_at')) and valid_email(record.get('confirmation_destination')))
    if not historical_email and not email_confirmed:missing.append({'key':'email_confirmation','label':'Confirmação do contrato por código enviado ao e-mail','group':'confirmation'})
    return missing


def compliance(db,client):
    record=effective_record(db,client)
    proof=db.execute('SELECT email,confirmed_at FROM client_contract_email_confirmations WHERE client_id=? AND acceptance_key=?',
                     (client['id'],acceptance_key(record))).fetchone()
    return record,diagnostics(client,record,bool(proof and valid_email(proof['email']) and valid_date(proof['confirmed_at'])))
