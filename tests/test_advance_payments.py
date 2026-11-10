"""Prepayment uses simulated FastFlow; no real charges or emails."""

import json
from datetime import datetime, timedelta
import pytest
from test_customer_payments import (
    payments,
    package,
    staff,
    deliver,
    create_pickup,
)
from test_plan import site, post_purchase
from test_app import token
from core import TZ
import plan_service as ps


def customer(client):
    with client.session_transaction() as s:
        s.clear()
        s.update(client_id=1, client_session_version=1)


def advance(client, ids, staff_mode=False, **extra):
    return client.post(
        "/painel/retirada/antecipar" if staff_mode else "/cliente/pacotes/pagar/gerar",
        data={"package_ids": ids, "csrf_token": token(client), **extra},
    )
