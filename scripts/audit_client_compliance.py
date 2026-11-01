"""Read-only diagnostics. Never prints names, emails, documents or signatures."""
import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import DatabaseConnection
from core import valid_cpf
from client_compliance import compliance, diagnostics

def audit(database,client_id=None):
    db=DatabaseConnection(database)
    try:
        db.execute('SET TRANSACTION READ ONLY' if db.postgres else 'PRAGMA query_only=ON')
        if db.postgres:
            available=bool(db.execute("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='client_contract_records'").fetchone())
        else:available=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='client_contract_records'").fetchone())
        rows=db.execute('SELECT * FROM clients'+(' WHERE id=?' if client_id is not None else ''),(client_id,) if client_id is not None else ()).fetchall()
        versions=Counter();blocking=Counter();technical=Counter();old_gate=Counter();history=Counter()
        for row in rows:
            owner=dict(row)
            version=owner.get('terms_version') or '(sem versão)'
            text=owner.get('terms_text')
            fingerprint=hashlib.sha256(text.encode()).hexdigest() if text else '(sem texto)'
            versions[(version,fingerprint)]+=1
            missing=compliance(db,owner)[1] if available else diagnostics(owner)
            blocking.update(item['key'] for item in missing)
            if not owner.get('active'):blocking.update(['account_inactive'])
            old_required=('name','cpf','phone','email','password_hash','terms_version','terms_text','signature','accepted_at','contract_id','contract_hash','confirmation_validated_at','confirmation_destination','residential_cep','residential_street','residential_number','residential_district','residential_city','residential_state')
            old_gate.update(key for key in old_required if not owner.get(key))
            if not (owner.get('signature') or '').startswith('data:image/png;base64,'):old_gate.update(['signature_png_prefix'])
            if owner.get('confirmation_channel')!='email':old_gate.update(['confirmation_channel_email'])
            if not valid_cpf(owner.get('cpf') or ''):old_gate.update(['cpf_invalid'])
            if available:
                for item in db.execute('SELECT terms_version,terms_text FROM client_contract_records WHERE client_id=?',(owner['id'],)).fetchall():
                    history[(item['terms_version'] or '(sem versão)', hashlib.sha256((item['terms_text'] or '').encode()).hexdigest())]+=1
            technical.update(key for key in ('contract_id','contract_hash','confirmation_channel','confirmation_destination','confirmation_validated_at') if not owner.get(key))
        return {'clients_checked':len(rows),'blocking_requirements':dict(blocking),'missing_legacy_metadata':dict(technical),'previous_google_gate_failures':dict(old_gate),
                'archived_versions':[{'version':v,'text_sha256':h,'count':n} for (v,h),n in history.items()],
                'original_versions':[{'version':v,'text_sha256':h,'count':n} for (v,h),n in versions.items()],
                'note':'Nenhum registro foi alterado. Metadados técnicos ausentes não equivalem automaticamente a aceite inválido.'}
    finally:db.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client-id',type=int,help='ID interno do cliente, opcional; resultado sem dados pessoais')
    args=parser.parse_args()
    database=os.environ.get('DATABASE_URL') or os.environ.get('DATABASE') or str(Path(__file__).resolve().parents[1]/'data'/'adelinha.db')
    try:
        result=audit(database,args.client_id)
    except Exception:
        print('Não foi possível consultar o banco. Confira configuração, acesso e atualização do schema; nenhum segredo ou dado pessoal foi exibido.',file=sys.stderr)
        sys.exit(1)
    print(json.dumps(result,ensure_ascii=False,indent=2))
