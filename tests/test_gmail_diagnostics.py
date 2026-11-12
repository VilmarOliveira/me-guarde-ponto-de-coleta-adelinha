from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs
import pytest
import notifications
import app as application
from test_app import app, client, login, token
from test_notifications import gmail_environment, Response


def test_diagnostic_checks_oauth_without_sending_email(monkeypatch):
    gmail_environment(monkeypatch)
    monkeypatch.setenv('GMAIL_CLIENT_ID', '  client-id  ')
    calls = []
    def request(req, timeout):
        calls.append(req.full_url)
        assert parse_qs(req.data.decode())['client_id'] == ['client-id']
        return Response({'access_token':'private-access-token'})
    monkeypatch.setattr(notifications, 'urlopen', request)
    ok, detail = notifications.gmail_authorization_status()
    assert ok and 'private-access-token' not in detail
    assert calls == ['https://oauth2.googleapis.com/token']


@pytest.mark.parametrize('code,field', [('invalid_client','GMAIL_CLIENT_SECRET'), ('invalid_grant','GMAIL_REFRESH_TOKEN')])
def test_safe_diagnostic_identifies_fields(monkeypatch, code, field):
    gmail_environment(monkeypatch)
    def fail(*args, **kwargs):
        raise HTTPError('https://oauth2.googleapis.com/token', 400, 'bad', {}, BytesIO(('{"error":"'+code+'","error_description":"SECRET-NEVER-SHOW"}').encode()))
    monkeypatch.setattr(notifications, 'urlopen', fail)
    ok, detail = notifications.gmail_authorization_status()
    assert not ok and field in detail and code in detail and 'SECRET-NEVER-SHOW' not in detail


def test_admin_diagnostic_requires_permission_csrf_and_has_cooldown(client, monkeypatch):
    calls = []
    monkeypatch.setattr(application, 'gmail_authorization_status', lambda: (calls.append(1) or True, 'Autorização validada'))
    path = '/painel/configuracoes/notificacoes'
    assert client.post(path, data={'csrf_token':token(client)}).status_code in (302, 403)
    assert not calls
    login(client)
    assert client.post(path, data={'csrf_token':'invalid'}).status_code == 400
    assert not calls
    result = client.post(path, data={'csrf_token':token(client)}, follow_redirects=True)
    assert 'Autorização validada' in result.text and len(calls) == 1
    client.post(path, data={'csrf_token':token(client)})
    assert len(calls) == 1


def test_recovery_uses_the_gmail_grant_and_accepted_message(client, monkeypatch):
    import base64, json, re
    from email import message_from_bytes
    from test_app import register
    gmail_environment(monkeypatch)
    register(client)
    captured = {}
    def request(req, timeout):
        if req.full_url.endswith('/token'):
            return Response({'access_token':'private-access-token'})
        assert req.full_url.endswith('/gmail/v1/users/me/messages/send')
        body = json.loads(req.data)
        mail = message_from_bytes(base64.urlsafe_b64decode(body['raw']))
        text = mail.get_payload(0).get_payload(decode=True).decode()
        captured['code'] = re.search(r'\b\d{6}\b', text).group()
        return Response({'id':'test-message-accepted'})
    monkeypatch.setattr(notifications, 'urlopen', request)
    sent = client.post('/cliente/recuperar-senha', data={
        'stage':'request', 'cpf':'52998224725', 'csrf_token':token(client),
    })
    assert sent.status_code == 200 and captured['code']
    changed = client.post('/cliente/recuperar-senha', data={
        'stage':'confirm','code':captured['code'],'password':'new-password-fixture',
        'password_confirm':'new-password-fixture','csrf_token':token(client),
    })
    assert changed.status_code == 302
    assert client.post('/cliente/login',data={'cpf':'52998224725','password':'new-password-fixture','csrf_token':token(client)}).status_code == 302
