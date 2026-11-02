"""Explicit deployment action for an existing contract; never runs on startup.

The default is a read-only report. --prepare creates/reuses a draft for review.
--publish additionally requires its exact preview hash and revision identifier.
No customer data, codes, credentials or signatures are printed; no emails sent.
"""
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from database import DatabaseConnection,is_postgres
from client_actions import current_contract,accepted_contract,prepare_current_reacceptance,publish_draft
from client_compliance import revision_id
from app import TERMS_VERSION,CONTRACT_LABEL,terms_text
from core import TZ


def run(database,summary=None,prepare=False,publish=False,expected_revision=None,preview_hash=None):
    if not is_postgres(database) and not Path(database).is_file():raise ValueError('Banco persistente não encontrado. Confira o ambiente e inicialize a aplicação atualizada antes de executar este comando.')
    db=DatabaseConnection(database)
    try:
        if not (prepare or publish):db.execute('SET TRANSACTION READ ONLY' if db.postgres else 'PRAGMA query_only=ON')
        default=(TERMS_VERSION,CONTRACT_LABEL,terms_text())
        version,label,text=current_contract(db,default)
        revision=revision_id(version,text)
        people=db.execute('SELECT * FROM clients WHERE active=1').fetchall()
        affected=sum(not accepted_contract(db,person,version,text) for person in people)
        print(f'Contrato: {label}\nCódigo técnico: {version}\nRevisão: {revision}\nClientes ativos sem aceite válido dessa revisão: {affected}')
        if not (prepare or publish):return
        if not expected_revision or expected_revision!=revision:raise ValueError('Informe --expected-revision exatamente como no relatório; o contrato pode ter mudado.')
        if not summary:raise ValueError('Informe --summary com o resumo real das mudanças.')
        if publish and not preview_hash:raise ValueError('Informe --preview-hash obtido na prévia do rascunho revisado.')
        rid=prepare_current_reacceptance(db,default,datetime.now(TZ).isoformat(),summary,'operador-cli',expected_revision)
        row=db.execute('SELECT status,content_hash FROM client_action_requests WHERE id=?',(rid,)).fetchone()
        print(f'Solicitação: {rid}\nEstado: {row["status"]}\nHash da prévia: {row["content_hash"]}\nPrévia: /painel/configuracoes/pendencias/{rid}/previa')
        if publish:
            publish_draft(db,rid,preview_hash,datetime.now(TZ).isoformat(),default)
            print('Exigência publicada. Nenhum aceite foi registrado em nome dos clientes.')
    finally:db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',default=os.environ.get('DATABASE_URL') or os.environ.get('DATABASE') or str(Path(__file__).resolve().parents[1]/'data'/'adelinha.db'))
    parser.add_argument('--summary');parser.add_argument('--prepare',action='store_true');parser.add_argument('--publish',action='store_true')
    parser.add_argument('--expected-revision');parser.add_argument('--preview-hash')
    args=parser.parse_args()
    try:run(**vars(args))
    except (ValueError,LookupError,RuntimeError) as error:parser.exit(1,str(error)+'\n')

if __name__=='__main__':main()
