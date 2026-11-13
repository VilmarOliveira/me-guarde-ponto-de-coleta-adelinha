"""Navigation and reversible archive operations: no real payments or messages."""
from datetime import datetime
import json

import pytest
import plan_service as ps
from core import TZ
from test_app import token
from test_customer_payments import payments, staff
from test_plan import site, post_purchase
from test_advance_payments import customer


def issue(client):
    url = post_purchase(client).location
    return url.rsplit('/',1)[-1]


def order(db, oid):
    return dict(db.execute('SELECT * FROM plan_orders WHERE id=?',(oid,)).fetchone())


def archive(client, oid):
    return client.post(f'/painel/configuracoes/plano/pagamentos/{oid}/arquivar',data={'csrf_token':token(client)})


def restore(client, oid):
    return client.post(f'/painel/configuracoes/plano/pagamentos/{oid}/restaurar',data={'csrf_token':token(client),'lista':'arquivadas'})


def test_archive_filters_and_restore_preserve_financial_data(payments):
    _, client, db, fake = payments
    old = issue(client)
    data = fake.records['1'].copy(); data['status']='expired'
    ps.apply_transaction(db,old,data,datetime.now(TZ))
    new = issue(client)
    before = order(db,old)
    staff(client)
    assert archive(client,old).status_code == 302
    assert archive(client,old).status_code == 302
    assert old not in client.get('/painel/configuracoes/plano').text
    assert new in client.get('/painel/configuracoes/plano').text
    archived = client.get('/painel/configuracoes/plano?lista=arquivadas').text
    assert old in archived and new not in archived and '>Restaurar</button>' in archived
    all_orders = client.get('/painel/configuracoes/plano?lista=todas').text
    assert old in all_orders and new in all_orders
    assert old in client.get('/painel/configuracoes/plano?arquivadas=1').text
    assert restore(client,old).status_code == 302
    assert restore(client,old).status_code == 302
    after = order(db,old)
    assert not after['archived_at']
    for field in ('state','amount_cents','transaction_id','paid_at','callback_url','expires_at','error_code'):
        assert after[field] == before[field]
    actions = [r['action'] for r in db.execute("SELECT action FROM audit_logs WHERE action IN ('arquivar_cobranca','restaurar_cobranca')").fetchall()]
    assert actions == ['arquivar_cobranca','restaurar_cobranca']
    assert not db.execute('SELECT 1 FROM plan_cycles').fetchone()


def test_restored_late_payment_remains_in_review_after_new_queries(payments):
    _, client, db, fake = payments
    oid = issue(client)
    staff(client); archive(client,oid)
    paid = fake.records['1'].copy(); paid['status']='paid'
    ps.apply_transaction(db,oid,paid,datetime.now(TZ))
    assert order(db,oid)['error_code'] == 'archived_payment_review'
    restore(client,oid)
    for _ in range(2): ps.apply_transaction(db,oid,paid,datetime.now(TZ))
    assert order(db,oid)['paid_at'] and order(db,oid)['error_code'] == 'archived_payment_review'
    assert not db.execute('SELECT 1 FROM plan_cycles').fetchone()


def test_restoring_unpaid_order_can_confirm_future_payment_normally(payments):
    _, client, db, fake = payments
    oid = issue(client)
    staff(client); archive(client,oid); restore(client,oid)
    paid = fake.records['1'].copy(); paid['status']='paid'
    ps.apply_transaction(db,oid,paid,datetime.now(TZ))
    ps.apply_transaction(db,oid,paid,datetime.now(TZ))
    assert db.execute('SELECT COUNT(*) n FROM plan_cycles WHERE order_id=?',(oid,)).fetchone()['n'] == 1


def test_archive_does_not_allow_second_pending_charge(payments):
    _, client, db, fake = payments
    oid = issue(client)
    staff(client); archive(client,oid)
    customer(client)
    assert issue(client) == oid and fake.creates == 1


def test_restore_requires_admin_and_csrf(payments):
    _, client, db, fake = payments
    oid = issue(client)
    path = f'/painel/configuracoes/plano/pagamentos/{oid}/restaurar'
    assert client.post(path,data={'csrf_token':token(client)}).status_code in (302,403)
    staff(client)
    assert client.get(path).status_code == 405
    assert client.post(path,data={'csrf_token':'wrong'}).status_code == 400
    assert client.get('/painel/configuracoes/plano?lista=invalid').status_code == 400


def test_history_has_only_month_and_range_fields_and_retains_legacy_day(payments):
    _, client, db, fake = payments
    staff(client)
    page = client.get('/painel/retiradas/historico').text
    assert 'Data específica' not in page and 'name="dia"' not in page
    assert all(f'name="{field}"' in page for field in ('mes','inicio','fim'))
    old = client.get('/painel/retiradas/historico?filtro=dia&dia=2026-11-10').text
    assert 'value="periodo" selected' in old
    assert old.count('value="2026-11-10"') == 2


def test_payment_method_display_order_and_mobile_login(payments):
    app, client, db, fake = payments
    home = app.test_client().get('/').text
    assert '> Login</a>' in home and '> LOGIN</a>' not in home
    staff(client)
    page = client.get('/painel').text
    positions = [page.index('name="payment_method" value="'+m+'"') for m in ('Pix Fast','Pix AI','Dinheiro')]
    assert positions == sorted(positions)
