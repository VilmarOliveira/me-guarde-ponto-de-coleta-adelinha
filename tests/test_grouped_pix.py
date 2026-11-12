"""Grouped checkout simulations; no real Fast charges or customer messages."""
from datetime import datetime, timedelta

import pytest
import pickup_payments as pp
import plan_service as ps
from core import TZ
from fastflow_gateway import GatewayError
from test_app import token
from test_customer_payments import payments, package, signed, staff, create_pickup
from test_plan import site
from test_advance_payments import customer


def create(client, ids, combine=False, **extra):
    return client.post('/cliente/pacotes/pagar/gerar', data={
        'package_ids': ids, 'csrf_token': token(client),
        **({'combine_pix': '1'} if combine else {}), **extra,
    })


def review(client, ids):
    return client.post('/cliente/pacotes/pagar', data={'package_ids': ids, 'csrf_token': token(client)})


def due(db, oid):
    db.execute('UPDATE pickup_payment_orders SET check_after=NULL WHERE id=?', (oid,))
    db.commit()


@pytest.mark.parametrize('count', [2, 3, 6])
def test_combines_any_selection_hides_old_and_reuses_new(payments, count):
    _, client, db, fake = payments
    ids = [package(db, number=i+1) for i in range(count)]
    old = create(client, ids[:1]).json
    page = review(client, ids)
    assert page.status_code == 200 and 'name="combine_pix"' in page.text
    new = create(client, ids, True).json
    assert new['id'] != old['id'] and fake.creates == 2
    stored = db.execute('SELECT * FROM pickup_payment_orders WHERE id=?', (old['id'],)).fetchone()
    assert stored['superseded_by'] == new['id'] and stored['qr_text']
    assert ps.payment_code(stored, datetime.now(TZ)) is None
    assert client.get(old['url']).location.endswith(new['id'])
    assert client.get(old['url']+'/status').json['done'] is True
    assert 'data-plan-pix' in client.get(new['url']).text
    assert db.execute('SELECT COUNT(*) n FROM pickup_payment_reservations WHERE order_id=?', (new['id'],)).fetchone()['n'] == count
    assert create(client, ids, True).json['id'] == new['id'] and fake.creates == 2
    fake.records['2']['status'] = 'paid'
    assert signed(client, '/webhooks/fastflow/retiradas/'+new['id'], '2').status_code == 200
    assert db.execute('SELECT SUM(paid_cents) n FROM package_prepayments').fetchone()['n'] == count*490


def test_combines_two_existing_pix_and_one_new_package(payments):
    _, client, db, fake = payments
    ids = [package(db, number=i) for i in range(1,4)]
    olds = [create(client, [pid]).json for pid in ids[:2]]
    assert review(client, ids).status_code == 200
    new = create(client, ids, True).json
    assert fake.creates == 3
    assert db.execute('SELECT COUNT(*) n FROM pickup_payment_orders WHERE superseded_by=?', (new['id'],)).fetchone()['n'] == 2
    assert all(client.get(o['url']).location.endswith(new['id']) for o in olds)


def test_paid_previous_is_deducted_before_creation(payments):
    _, client, db, fake = payments
    ids = [package(db, number=i) for i in (1,2)]
    old = create(client, ids[:1]).json
    fake.records['1']['status'] = 'paid'
    due(db, old['id'])
    assert review(client, ids).status_code == 200
    new = create(client, ids).json
    row = db.execute('SELECT * FROM pickup_payment_orders WHERE id=?', (new['id'],)).fetchone()
    assert row['amount_cents'] == 490
    assert db.execute('SELECT SUM(paid_cents) n FROM package_prepayments').fetchone()['n'] == 490


@pytest.mark.parametrize('case', ['unavailable', 'mismatch', 'under_review', 'approved', 'partial'])
def test_does_not_replace_unverified_or_partial_charge(payments, monkeypatch, case):
    _, client, db, fake = payments
    ids = [package(db, number=i) for i in (1,2,3)]
    old = create(client, ids[:2] if case == 'partial' else ids[:1]).json
    due(db, old['id'])
    if case == 'unavailable':
        monkeypatch.setattr(fake, 'get', lambda tid: (_ for _ in ()).throw(GatewayError()))
    elif case == 'mismatch': fake.records['1']['amount'] = 999
    elif case in ('under_review', 'approved'): fake.records['1']['status'] = case
    selected = [ids[0], ids[2]] if case == 'partial' else ids
    assert create(client, selected, True).status_code == 400
    assert fake.creates == 1
    assert db.execute('SELECT superseded_by FROM pickup_payment_orders WHERE id=?', (old['id'],)).fetchone()['superseded_by'] is None
    assert db.execute('SELECT 1 FROM pickup_payment_reservations WHERE order_id=?', (old['id'],)).fetchone()


def test_prior_payment_quarantines_current_qr_and_does_not_auto_settle(payments):
    _, client, db, fake = payments
    ids = [package(db, number=i) for i in (1,2,3)]
    old = create(client, ids[:1]).json
    middle = create(client, ids[:2], True).json
    new = create(client, ids, True).json
    fake.records['1']['status'] = 'paid'
    assert signed(client, '/webhooks/fastflow/retiradas/'+old['id'], '1').status_code == 200
    assert 'data-plan-pix' not in client.get(new['url']).text
    assert 'Pagamento identificado em uma cobrança anterior' in client.get(new['url']).text
    assert not db.execute('SELECT 1 FROM package_prepayments').fetchone()
    assert create(client, ids, True).status_code == 400
    fake.records['3']['status'] = 'paid'
    assert signed(client, '/webhooks/fastflow/retiradas/'+new['id'], '3').status_code == 200
    assert not db.execute('SELECT 1 FROM package_prepayments').fetchone()
    assert db.execute('SELECT error_code FROM pickup_payment_orders WHERE id=?', (new['id'],)).fetchone()['error_code'] == 'prior_payment_review'
    with pytest.raises(ValueError, match='cobrança anterior'):
        pp.assert_unreserved(db, ids, new['id'])


def test_failed_creation_restores_previous_visible_pix(payments, monkeypatch):
    _, client, db, fake = payments
    ids = [package(db, number=i) for i in (1,2)]
    old = create(client, ids[:1]).json
    monkeypatch.setattr(fake, 'create', lambda *a, **k: (_ for _ in ()).throw(GatewayError()))
    result = create(client, ids, True)
    assert result.status_code == 200
    prior = db.execute('SELECT * FROM pickup_payment_orders WHERE id=?', (old['id'],)).fetchone()
    assert prior['superseded_by'] is None and prior['superseded_at'] is None
    assert 'data-plan-pix' in client.get(old['url']).text
    assert db.execute('SELECT order_id FROM pickup_payment_reservations').fetchone()['order_id'] == old['id']


def test_amount_changed_between_review_and_create_requires_review(payments):
    _, client, db, fake = payments
    ids = [package(db, number=i) for i in (1,2)]
    old = create(client, ids[:1]).json
    assert review(client, ids).status_code == 200
    fake.records['1']['status'] = 'paid'
    due(db, old['id'])
    result = create(client, ids, True, expected_total='980')
    assert result.status_code == 400 and 'saldo mudou' in result.json['error']
    assert fake.creates == 1


def test_paid_counter_charge_is_settled_before_group_balance(payments):
    _, client, db, fake = payments
    ids = [package(db, number=i) for i in (1,2)]
    staff(client)
    old = create_pickup(client, ids[:1]).json
    fake.records['1']['status'] = 'paid'
    due(db, old['id'])
    customer(client)
    assert review(client, ids).status_code == 200
    new = create(client, ids).json
    assert db.execute('SELECT amount_cents FROM pickup_payment_orders WHERE id=?', (new['id'],)).fetchone()['amount_cents'] == 490


def test_polling_current_charge_checks_the_hidden_previous_charge(payments):
    _, client, db, fake = payments
    ids = [package(db, number=i) for i in (1,2)]
    old = create(client, ids[:1]).json
    new = create(client, ids, True).json
    fake.records['1']['status'] = 'paid'
    due(db, old['id'])
    assert client.get(new['url']+'/status').json['pix_available'] is False
    assert 'data-plan-pix' not in client.get(new['url']).text


def test_uncertain_creation_preserves_group_reservation(payments, monkeypatch):
    _, client, db, fake = payments
    ids = [package(db, number=i) for i in (1,2)]
    old = create(client, ids[:1]).json
    fake.failure = GatewayError(uncertain=True)
    new = create(client, ids, True).json
    assert db.execute('SELECT superseded_by FROM pickup_payment_orders WHERE id=?', (old['id'],)).fetchone()['superseded_by'] == new['id']
    assert db.execute('SELECT COUNT(*) n FROM pickup_payment_reservations WHERE order_id=?', (new['id'],)).fetchone()['n'] == 2
    assert create(client, ids, True).json['id'] == new['id'] and fake.creates == 2


@pytest.mark.skipif(not __import__('os').environ.get('TEST_DATABASE_URL'), reason='PostgreSQL isolado não configurado')
def test_postgres_concurrent_grouping_creates_only_one_new_pix(monkeypatch):
    import os, uuid
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    import psycopg
    from psycopg import sql
    import plan_routes
    from app import create_app
    from database import DatabaseConnection
    from test_app import register
    from test_plan import CONFIG, FakeFlow
    schema = 'grouped_' + uuid.uuid4().hex
    with psycopg.connect(os.environ['TEST_DATABASE_URL'], autocommit=True) as control:
        control.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        db = None
        try:
            url = urlsplit(os.environ['TEST_DATABASE_URL'])
            query = [(k,v) for k,v in parse_qsl(url.query) if k != 'options']
            path = urlunsplit((url.scheme,url.netloc,url.path,urlencode(query+[('options','-csearch_path='+schema)]),url.fragment))
            app = create_app({**CONFIG, 'DATABASE':path})
            client = app.test_client()
            register(client)
            db = DatabaseConnection(path)
            fake = FakeFlow()
            original = fake.create
            def issue(order, owner, card=None):
                result = original(order, owner, card)
                fake.records[result['id']]['amount'] = order['amount_cents']/100
                return fake.records[result['id']].copy()
            fake.create = issue
            monkeypatch.setattr(plan_routes,'gateway',lambda app:fake)
            ids = [package(db,number=i) for i in (1,2,3)]
            customer(client)
            old = create(client,ids[:1]).json
            def grouped(_):
                c = app.test_client()
                customer(c)
                result = create(c,ids,True)
                assert result.status_code == 200, result.text
                return result.json['id']
            with ThreadPoolExecutor(max_workers=3) as pool:
                created = list(pool.map(grouped,range(3)))
            assert len(set(created)) == 1 and fake.creates == 2
            assert db.execute('SELECT superseded_by FROM pickup_payment_orders WHERE id=?',(old['id'],)).fetchone()['superseded_by'] == created[0]
            assert db.execute('SELECT COUNT(*) n FROM pickup_payment_reservations WHERE order_id=?',(created[0],)).fetchone()['n'] == 3
        finally:
            if db: db.close()
            control.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def test_database_error_rolls_back_replacement_without_losing_old_pix(payments, monkeypatch):
    _, client, db, fake = payments
    ids = [package(db, number=i) for i in (1,2)]
    old = create(client, ids[:1]).json
    from database import DatabaseConnection, DatabaseIntegrityError
    execute = DatabaseConnection.execute
    def failing(self, statement, params=()):
        if 'INSERT INTO pickup_payment_packages' in statement and params[0] != old['id']:
            raise DatabaseIntegrityError()
        return execute(self, statement, params)
    monkeypatch.setattr(DatabaseConnection, 'execute', failing)
    assert create(client, ids, True).status_code == 400
    assert fake.creates == 1
    assert db.execute('SELECT COUNT(*) n FROM pickup_payment_orders').fetchone()['n'] == 1
    assert db.execute('SELECT superseded_by FROM pickup_payment_orders WHERE id=?', (old['id'],)).fetchone()['superseded_by'] is None
    assert db.execute('SELECT order_id FROM pickup_payment_reservations').fetchone()['order_id'] == old['id']


@pytest.mark.parametrize('adjustment', [{'surcharge_amount':'2.00','surcharge_reason':'Ajuste confirmado pela equipe'}, {'discount_amount':'0.90'}])
def test_pending_manual_adjustments_are_not_discarded_by_grouping(payments, adjustment):
    _, client, db, fake = payments
    ids = [package(db, number=i) for i in (1,2)]
    staff(client)
    old = create_pickup(client, ids[:1], **adjustment).json
    customer(client)
    result = create(client, ids, True)
    assert result.status_code == 400 and 'ajuste da equipe' in result.json['error']
    assert fake.creates == 1
    assert db.execute('SELECT superseded_by FROM pickup_payment_orders WHERE id=?', (old['id'],)).fetchone()['superseded_by'] is None
