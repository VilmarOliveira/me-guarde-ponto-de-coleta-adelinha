"""Explicit one-time bootstrap of a separate, initialized PostgreSQL database.

Copies presentation/configuration and one primary administrator, never business
records. Does not switch the live EnvironmentFile or restart any service.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

MARKER = 'clean_production_bootstrap_v1'
CONFIG_TABLES = ('site_themes', 'settings', 'site_banners', 'site_photos',
                 'explanatory_cards', 'blog_icon_assets', 'blog_icons',
                 'blog_posts', 'customer_payment_policies')
SEED_TABLES = {'settings', 'explanatory_cards', 'blog_posts',
               'customer_payment_policies', 'system_migrations', 'plan_gateway_lock'}
DEFAULT_SLUGS = {'receber-compras-online-em-franca',
    'como-funciona-me-guarde-ponto-de-coleta-adelinha',
    'endereco-para-receber-compras-adelinha', 'quanto-custa-guardar-encomenda',
    'retirada-de-pacotes-por-outra-pessoa'}


class PreparationError(Exception):
    """Safe, credential-free operator message."""


def read_env(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep:
            raise PreparationError('Arquivo de ambiente com linha inválida.')
        key = key.strip()
        if key not in {'DATABASE_URL', 'SECRET_KEY'}:
            continue
        if key in result:
            raise PreparationError('Há variáveis duplicadas no arquivo de ambiente.')
        parts = shlex.split(value.strip(), comments=False)
        if len(parts) > 1:
            raise PreparationError('Valor de ambiente precisa de aspas.')
        result[key] = parts[0] if parts else ''
    if not result.get('DATABASE_URL'):
        raise PreparationError('DATABASE_URL ausente.')
    return result


def identity(url):
    c = conninfo_to_dict(url)
    if not c.get('host') or not c.get('dbname'):
        raise PreparationError('Use conexão PostgreSQL completa.')
    return (c['host'].lower().replace('-pooler.', '.'), c['dbname'], c.get('port', '5432'))


def tables(db):
    return [r['table_name'] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")]


def columns(db, table):
    return [r['column_name'] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position", (table,))]


def qualified(table):
    return sql.Identifier('public', table)


def count(db, table):
    return db.execute(sql.SQL('SELECT count(*) n FROM {}').format(qualified(table))).fetchone()['n']


def insert(db, table, row):
    db.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
        qualified(table), sql.SQL(',').join(map(sql.Identifier, row)),
        sql.SQL(',').join(sql.Placeholder() for _ in row)), list(row.values()))


def copy_table(source, target, table):
    if set(columns(source, table)) != set(columns(target, table)):
        raise PreparationError('Estrutura diferente na configuração: ' + table)
    rows = list(source.execute(sql.SQL('SELECT * FROM {}').format(qualified(table))))
    target.execute(sql.SQL('DELETE FROM {}').format(qualified(table)))
    for row in rows:
        if 'created_by' in row:
            row['created_by'] = '1'
        if table == 'settings':
            row['legacy_staff_active'] = 0
        insert(target, table, row)
    return len(rows)


def assert_fresh(target, names):
    if 'system_migrations' not in names:
        raise PreparationError('Inicialize as tabelas do destino primeiro.')
    if target.execute('SELECT 1 FROM system_migrations WHERE name=%s', (MARKER,)).fetchone():
        raise PreparationError('Produção já preparada; nenhuma cópia repetida foi feita.')
    for table in names:
        if table not in SEED_TABLES and count(target, table):
            raise PreparationError('Destino não está vazio: ' + table)
    slugs = {r['slug'] for r in target.execute('SELECT slug FROM blog_posts')}
    if not slugs <= DEFAULT_SLUGS:
        raise PreparationError('Destino já contém artigos próprios; não será sobrescrito.')


def reseed(target, table):
    sequence = target.execute('SELECT pg_get_serial_sequence(%s,%s) seq', ('public.'+table, 'id')).fetchone()['seq']
    if sequence:
        maximum = target.execute(sql.SQL('SELECT max(id) n FROM {}').format(qualified(table))).fetchone()['n']
        target.execute('SELECT setval(%s::regclass,%s,%s)', (sequence, maximum or 1, maximum is not None))


def preserve_contract(source, target, stamp):
    current = source.execute("SELECT * FROM client_action_requests WHERE is_contract=1 AND published_at IS NOT NULL AND starts_at<=%s ORDER BY published_at DESC,id DESC LIMIT 1", (stamp,)).fetchone()
    if not current:
        return False  # The application's unchanged built-in contract remains current.
    # Carry the document, not the old campaign/audience, assignments or acceptances.
    data = dict(title='Contrato inicial da produção', explanation='Condições vigentes do serviço.',
        body=current['body'], kind='mandatory', audience='specific', clients_json='[]', criterion=None,
        actions_json=json.dumps(['terms','signature','email']), functions_json='[]', starts_at=stamp,
        due_at=None, is_contract=1, require_existing=0, document_version=current['document_version'],
        document_label=current['document_label'])
    data['content_hash'] = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    insert(target, 'client_action_requests', dict(id=1, family_key='production-initial-contract',
        version=1, status='published', created_at=stamp, created_by='1', published_at=stamp, **data))
    insert(target, 'client_published_contract_versions', dict(version=current['document_version'], body=current['body'], request_id=1))
    return True


def prepare(source_url, target_url, *, apply=False, before_commit=None):
    if identity(source_url) == identity(target_url):
        raise PreparationError('Origem e destino precisam ser bancos diferentes.')
    stamp = datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat()
    with psycopg.connect(source_url, connect_timeout=30, row_factory=dict_row) as source:
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        source.execute('SET LOCAL search_path TO public, pg_catalog')
        source.execute("SET LOCAL statement_timeout TO '120s'")
        with psycopg.connect(target_url, connect_timeout=30, row_factory=dict_row) as target:
            target.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
            target.execute('SET LOCAL search_path TO public, pg_catalog')
            target.execute("SET LOCAL lock_timeout TO '10s'")
            target.execute("SET LOCAL statement_timeout TO '120s'")
            target.execute('SELECT pg_advisory_xact_lock(726348291)')
            names = tables(target)
            if not set(CONFIG_TABLES) <= set(names):
                raise PreparationError('Tabelas de configuração ausentes no destino.')
            target.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(sql.SQL(',').join(qualified(n) for n in names)))
            assert_fresh(target, names)
            admins = list(source.execute("SELECT * FROM staff_users WHERE role='administrador' AND is_primary=1 AND active=1 AND deleted_at IS NULL"))
            if len(admins) != 1 or not admins[0]['password_hash']:
                raise PreparationError('Origem precisa ter exatamente um administrador principal ativo com senha.')
            if not admins[0]['can_settings'] or not admins[0]['can_users']:
                raise PreparationError('Confira as permissões do administrador principal antes da cópia.')
            if count(source, 'settings') != 1:
                raise PreparationError('Configuração principal da origem inválida.')
            if not apply:
                return {'mode':'conferencia', 'administrator':'ADMPCA-1', 'business_records':0}
            counts = {table:copy_table(source, target, table) for table in CONFIG_TABLES}
            admin = dict(admins[0]); admin.update(id=1, public_id='ADMPCA-1', created_at=stamp,
                last_login_at=None, session_version=int(admin['session_version'])+1)
            insert(target, 'staff_users', admin)
            insert(target, 'id_sequences', dict(prefix='ADMPCA', last_value=1))
            contract = preserve_contract(source, target, stamp)
            untouched = set(names)-set(CONFIG_TABLES)-{'staff_users','id_sequences','client_action_requests',
                'client_published_contract_versions','system_migrations','plan_gateway_lock'}
            if any(count(target, table) for table in untouched):
                raise PreparationError('Destino contém dados operacionais inesperados.')
            insert(target, 'system_migrations', dict(name=MARKER, applied_at=stamp))
            for table in names:
                if 'id' in columns(target, table):
                    reseed(target, table)
            if before_commit:
                before_commit()
            return {'mode':'preparado', 'administrator':'ADMPCA-1', 'business_records':0,
                    'configuration_counts':counts, 'published_contract_preserved':contract}


def rotate_staged_key(path):
    path = Path(path)
    lines = path.read_text().splitlines()
    replacement = 'SECRET_KEY='+secrets.token_urlsafe(48)
    replaced = False
    for n, line in enumerate(lines):
        if line.partition('=')[0].strip() == 'SECRET_KEY':
            lines[n] = replacement; replaced = True
    if not replaced:
        lines.append(replacement)
    fd, temporary = tempfile.mkstemp(prefix='.production-env-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write('\n'.join(lines)+'\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def verify_backup(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size < 100:
        raise PreparationError('Informe o backup completo já criado.')
    restore = '/usr/lib/postgresql/18/bin/pg_restore'
    if not Path(restore).is_file(): restore = shutil.which('pg_restore')
    if not restore:
        raise PreparationError('pg_restore não encontrado para conferir o backup.')
    result = subprocess.run([restore, '--list', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode:
        raise PreparationError('Backup não passou na verificação de leitura.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-env', default='/etc/ponto-coleta/site.env')
    parser.add_argument('--target-env', default='/etc/ponto-coleta/site-producao.env')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        if os.path.samefile(args.source_env, args.target_env):
            raise PreparationError('Não use o mesmo arquivo de ambiente como origem e destino.')
        source, target = read_env(args.source_env), read_env(args.target_env)
        if args.apply:
            if not args.backup: raise PreparationError('Informe --backup antes de aplicar.')
            verify_backup(args.backup)
        result = prepare(source['DATABASE_URL'], target['DATABASE_URL'], apply=args.apply,
            before_commit=(lambda:rotate_staged_key(args.target_env)) if args.apply else None)
        print(json.dumps(result, ensure_ascii=False))
        print('Banco antigo e serviço ativo não foram alterados.')
        return 0
    except PreparationError as error:
        print(str(error), file=sys.stderr)
    except Exception:
        # Connection strings, SQL values, password hashes and provider keys stay private.
        print('Preparação não concluída. Nenhum serviço foi trocado; confira os arquivos e conexões.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
