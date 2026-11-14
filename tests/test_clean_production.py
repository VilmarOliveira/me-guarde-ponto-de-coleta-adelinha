"""Fresh production bootstrap checks: isolated local CI databases, no providers."""
import os
from pathlib import Path
import secrets

import psycopg
from psycopg import sql
from urllib.parse import urlsplit, urlunsplit
from psycopg.rows import dict_row
import pytest
from werkzeug.security import generate_password_hash, check_password_hash

from app import init_db, next_public_code
from database import DatabaseConnection
from scripts import prepare_clean_production as clean


@pytest.fixture
def databases():
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('PostgreSQL local de CI não configurado')
    names = ['clean_source_'+secrets.token_hex(6), 'clean_target_'+secrets.token_hex(6)]
    admin = psycopg.connect(url, autocommit=True)
    urls = []
    try:
        for name in names:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
            urls.append(urlunsplit(urlsplit(url)._replace(path='/'+name)))
        for item in urls:
            assert init_db(item)
        with psycopg.connect(urls[0], row_factory=dict_row) as db:
            db.execute("INSERT INTO clients(id,public_id,name,cpf,phone,terms_version,signature,accepted_at) VALUES(9,'CLIPCA-99','Cliente fictício','00000000000','16999999999','v1','fixture','2026-01-01')")
            db.execute("INSERT INTO staff_users(id,public_id,name,email,password_hash,role,is_primary,active,can_settings,can_users,created_at,session_version) VALUES(7,'ADMPCA-77','Administrador fictício','primary@example.test',%s,'administrador',1,1,1,1,'2026-01-01',4)", (generate_password_hash('fixture-password'),))
            db.execute("INSERT INTO staff_users(id,public_id,name,email,password_hash,role,is_primary,created_at) VALUES(99,'ATEPCA-88','Outro usuário','other@example.test','fixture','atendente',0,'2026-01-01')")
            db.execute("INSERT INTO id_sequences(prefix,last_value) VALUES('CLIPCA',99),('ADMPCA',77),('ATEPCA',88)")
            db.execute("INSERT INTO packages(id,public_id,client_id,width,height,length,weight,shelf,category,base_price,status,created_at) VALUES(41,'PCTPCA-41',9,10,10,10,1,'A1','Pequeno',4.9,'aguardando retirada','2026-01-01')")
            db.execute("INSERT INTO plan_orders(id,client_id,method,amount_cents,cycle_key,state,created_at,payer_document,callback_url,rules_version,rules_text,accepted_at) VALUES('old-payment',9,'pix',3390,'fixture','pending','2026-01-01','00000000000','https://example.test/webhook','v1','fixture','2026-01-01')")
            db.execute("INSERT INTO site_themes(id,name,colors,created_at,created_by) VALUES(5,'Tema aprovado','{}','2026-01-01','7')")
            db.execute("UPDATE settings SET header_image=%s,header_image_mime='image/png',active_theme_id=5,monthly_price_cents=4390,pix_key='fixture@example.test' WHERE id=1", (b'fixture-image-bytes',))
            db.execute("INSERT INTO blog_posts(id,slug,title,summary,body,status,updated_at) VALUES(42,'publicado-fixture','Artigo aprovado','Resumo','Conteúdo','published','2026-01-01')")
            db.execute("INSERT INTO blog_icon_assets(digest,image,mime) VALUES('fixture-digest',%s,'image/png')", (b'icon-fixture',))
            db.execute("INSERT INTO blog_icons(identifier,name,asset_hash,updated_at) VALUES('custom-fixture','Ícone','fixture-digest','2026-01-01')")
            db.execute("INSERT INTO client_action_requests(id,family_key,version,status,title,explanation,body,kind,audience,clients_json,actions_json,functions_json,starts_at,is_contract,require_existing,document_version,document_label,content_hash,created_at,created_by,published_at) VALUES(55,'old-family',2,'published','Exigência antiga','Texto','Contrato aprovado exato','mandatory','specific','[9]','[\"terms\"]','[\"delivery\"]','2026-01-01',1,1,'APPROVED-1','Contrato aprovado','old-hash','2026-01-01','7','2026-01-01')")
            db.execute("INSERT INTO client_action_assignments(request_id,client_id,assigned_at) VALUES(55,9,'2026-01-01')")
        yield urls
    finally:
        for name in names:
            admin.execute(sql.SQL('DROP DATABASE IF EXISTS {} WITH (FORCE)').format(sql.Identifier(name)))
        admin.close()


def fetch(url, query, params=()):
    with psycopg.connect(url, row_factory=dict_row) as db:
        return db.execute(query, params).fetchall()


def test_clean_copy_preserves_configuration_and_only_owner(databases, tmp_path):
    source, target = databases
    old_file=tmp_path/'live.env'; stage=tmp_path/'staged.env'
    old_text='DATABASE_URL="'+source+'"\nSECRET_KEY=old-session-key\nSTAFF_NAME=Ponto de Coleta Adelinha\nFAST_PIX_API_KEY=fixture-provider-key\n'
    old_file.write_text(old_text)
    stage.write_text(old_text.replace(source,target))
    result=clean.prepare(source,target,apply=True,before_commit=lambda:clean.rotate_staged_key(stage))
    assert result['administrator']=='ADMPCA-1'
    assert result['business_records']==0
    assert result['published_contract_preserved']
    assert old_file.read_text()==old_text
    assert clean.read_env(stage)['SECRET_KEY']!='old-session-key'
    assert clean.read_env(stage)['DATABASE_URL']==target
    assert 'FAST_PIX_API_KEY=fixture-provider-key' in stage.read_text()
    assert stage.stat().st_mode & 0o777 == 0o600
    owner=fetch(target,'SELECT * FROM staff_users')[0]
    assert owner['id']==1 and owner['public_id']=='ADMPCA-1' and owner['session_version']==5
    assert check_password_hash(owner['password_hash'],'fixture-password')
    assert owner['last_login_at'] is None and owner['email']=='primary@example.test'
    settings=fetch(target,'SELECT * FROM settings')[0]
    assert settings['monthly_price_cents']==4390 and bytes(settings['header_image'])==b'fixture-image-bytes'
    assert settings['legacy_staff_active']==0 and settings['active_theme_id']==5
    assert fetch(target,'SELECT created_by FROM site_themes')[0]['created_by']=='1'
    assert fetch(target,"SELECT slug FROM blog_posts WHERE id=42")[0]['slug']=='publicado-fixture'
    assert fetch(target,'SELECT asset_hash FROM blog_icons')[0]['asset_hash']=='fixture-digest'
    contract=fetch(target,'SELECT * FROM client_action_requests')[0]
    assert contract['body']=='Contrato aprovado exato' and contract['document_version']=='APPROVED-1'
    assert contract['id']==1 and contract['audience']=='specific' and contract['clients_json']=='[]'
    assert contract['functions_json']=='[]' and contract['require_existing']==0
    assert fetch(target,'SELECT request_id FROM client_published_contract_versions')[0]['request_id']==1
    for table in ['clients','packages','pickups','plan_orders','plan_cycles','plan_credit_ledger',
                  'pickup_payment_orders','package_prepayments','audit_logs','login_attempts',
                  'google_client_identities','client_contract_records','client_action_assignments',
                  'client_action_responses','staff_recovery_codes','analytics_events']:
        assert fetch(target,sql.SQL('SELECT count(*) n FROM {}').format(sql.Identifier(table)))[0]['n']==0,table
    assert fetch(source,'SELECT public_id FROM staff_users WHERE id=7')[0]['public_id']=='ADMPCA-77'
    assert fetch(source,'SELECT count(*) n FROM clients')[0]['n']==1
    assert fetch(source,'SELECT count(*) n FROM client_action_assignments')[0]['n']==1
    conn=DatabaseConnection(target)
    for prefix,value in [('CLIPCA',1),('ATEPCA',1),('GERPCA',1),('ADMPCA',2)]:
        assert next_public_code(conn,prefix)==f'{prefix}-{value}'
    conn.rollback();conn.close()
    with psycopg.connect(target) as db:
        assert db.execute("SELECT nextval(pg_get_serial_sequence('packages','id'))").fetchone()[0]==1
        assert db.execute("SELECT nextval(pg_get_serial_sequence('staff_users','id'))").fetchone()[0]==2
        assert db.execute("SELECT nextval(pg_get_serial_sequence('blog_posts','id'))").fetchone()[0]==43
    with pytest.raises(clean.PreparationError,match='já preparada'):
        clean.prepare(source,target,apply=True)


def test_guards_and_dry_run_never_overwrite(databases):
    source,target=databases
    with pytest.raises(clean.PreparationError,match='diferentes'):
        clean.prepare(source,source,apply=True)
    assert clean.prepare(source,target)['mode']=='conferencia'
    assert fetch(target,'SELECT count(*) n FROM staff_users')[0]['n']==0
    with psycopg.connect(target) as db:
        db.execute("INSERT INTO clients(name,cpf,phone,terms_version,signature,accepted_at) VALUES('Novo cliente real','00000000000','1','v1','fixture','2026-01-01')")
    with pytest.raises(clean.PreparationError,match='não está vazio'):
        clean.prepare(source,target,apply=True)
    assert fetch(target,'SELECT monthly_price_cents FROM settings')[0]['monthly_price_cents']==3390
    assert fetch(target,'SELECT name FROM clients')[0]['name']=='Novo cliente real'


def test_copy_failure_rolls_back_target_and_source_is_read_only(databases, monkeypatch):
    source,target=databases
    original=clean.copy_table
    def forbidden_write(src,dst,table):
        if table=='blog_posts':
            src.execute('UPDATE settings SET monthly_price_cents=1 WHERE id=1')
        return original(src,dst,table)
    monkeypatch.setattr(clean,'copy_table',forbidden_write)
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        clean.prepare(source,target,apply=True)
    assert fetch(source,'SELECT monthly_price_cents FROM settings')[0]['monthly_price_cents']==4390
    assert fetch(target,'SELECT monthly_price_cents FROM settings')[0]['monthly_price_cents']==3390
    assert fetch(target,'SELECT count(*) n FROM staff_users')[0]['n']==0
    assert not fetch(target,'SELECT name FROM system_migrations WHERE name=%s',(clean.MARKER,))


def test_primary_owner_required(databases):
    source,target=databases
    with psycopg.connect(source) as db:
        db.execute('UPDATE staff_users SET is_primary=0 WHERE id=7')
    with pytest.raises(clean.PreparationError,match='exatamente um'):
        clean.prepare(source,target,apply=True)
    assert fetch(target,'SELECT count(*) n FROM staff_users')[0]['n']==0


def test_env_parser_and_staged_secret(tmp_path):
    file=tmp_path/'stage.env'
    file.write_text('DATABASE_URL="postgresql://fixture@localhost/newdb"\nSTAFF_NAME=Ponto de Coleta Adelinha\nSECRET_KEY=original\nFAST_PIX_WEBHOOK_SECRET=unchanged\n')
    clean.rotate_staged_key(file)
    result=clean.read_env(file)
    assert result['SECRET_KEY']!='original'
    assert 'FAST_PIX_WEBHOOK_SECRET=unchanged' in file.read_text()
    assert 'STAFF_NAME=Ponto de Coleta Adelinha' in file.read_text()
    with file.open('a') as f:f.write('DATABASE_URL=postgresql://fixture@localhost/other\n')
    with pytest.raises(clean.PreparationError,match='duplicadas'):
        clean.read_env(file)


def test_cli_rejects_same_file_and_missing_backup_without_leaks(tmp_path,capsys):
    source=tmp_path/'source.env'; target=tmp_path/'target.env'
    source.write_text('DATABASE_URL=postgresql://fixture:private-password@localhost/source\n')
    target.write_text(source.read_text().replace('/source','/target'))
    assert clean.main(['--source-env',str(source),'--target-env',str(source)])==1
    assert clean.main(['--source-env',str(source),'--target-env',str(target),'--apply'])==1
    output=capsys.readouterr()
    assert 'mesmo arquivo' in output.err and '--backup' in output.err
    assert 'private-password' not in output.err+output.out
