"""Integrações HTTPS de notificação sem simular sucesso de entrega."""
import base64
import json
import os
import re
from html import escape
from branding import DEFAULT_THEME, GRAPHITE, ORANGE, TEAL, ORIGINAL_LOGO
from email.message import EmailMessage
from email.utils import formataddr
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_SENDER = "pontodecoletaadelinha@gmail.com"
GMAIL_DISPLAY_NAME = "Ponto de Coleta Adelinha"
EMAIL_SIGNATURE = "ME GUARDE — COMPROU? DEIXA COM A GENTE."


def notification_config_status():
    """Retorna o estado da configuração sem expor segredos."""
    email_ready = all((os.getenv("GMAIL_CLIENT_ID", "").strip(), os.getenv("GMAIL_CLIENT_SECRET", "").strip(),
                       os.getenv("GMAIL_REFRESH_TOKEN", "").strip())) and os.getenv("GMAIL_SENDER", GMAIL_SENDER).strip().lower() == GMAIL_SENDER
    whatsapp_ready = all((os.getenv("WHATSAPP_ACCESS_TOKEN"), os.getenv("WHATSAPP_PHONE_NUMBER_ID"),
                          os.getenv("WHATSAPP_TEMPLATE_NAME"), os.getenv("WHATSAPP_GRAPH_VERSION")))
    return {"email": bool(email_ready), "whatsapp": bool(whatsapp_ready)}


def normalize_whatsapp_destination(destination):
    """Converte números brasileiros locais para E.164, sem o caractere +."""
    value = re.sub(r"\D", "", destination or "")
    if len(value) in (10, 11):
        value = "55" + value
    if not 10 <= len(value) <= 15:
        raise ValueError("número de WhatsApp inválido")
    return value


def _json_http_error(error, provider):
    try:
        body = json.loads(error.read().decode("utf-8", errors="replace"))
        info = body.get("error", {})
        if isinstance(info, str):
            message, code = info, error.code
        else:
            message = str(info.get("message", "solicitação recusada"))[:240]
            code = info.get("code", error.code)
        return f"{provider} recusou a solicitação ({code}): {message}"
    except Exception:
        return f"{provider} recusou a solicitação (HTTP {error.code})"


def _gmail_access_token():
    client_id = os.getenv("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.getenv("GMAIL_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("GMAIL_REFRESH_TOKEN", "").strip()
    if not all((client_id, client_secret, refresh_token)):
        return None, "Gmail não configurado: confira Client ID, Client Secret e Refresh Token"
    request = Request("https://oauth2.googleapis.com/token", data=urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode("ascii"), headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
        token = result.get("access_token") if isinstance(result, dict) else None
        if not isinstance(token, str) or not token.strip():
            return None, "O Google não retornou um token de acesso"
        return token, None
    except HTTPError as error:
        try:
            code = json.loads(error.read().decode("utf-8")).get("error")
        except (ValueError, UnicodeError, AttributeError):
            code = None
        code = code if isinstance(code, str) else None
        hints = {
            "invalid_client": "invalid_client: confira GMAIL_CLIENT_ID e GMAIL_CLIENT_SECRET do mesmo cliente OAuth",
            "invalid_grant": "invalid_grant: renove GMAIL_REFRESH_TOKEN usando esse mesmo ID e segredo",
            "unauthorized_client": "unauthorized_client: confira a autorização do cliente OAuth para envio de e-mails",
        }
        return None, f"OAuth do Google recusou a autorização ({error.code}). " + hints.get(code, "Confira a configuração do Gmail no servidor")
    except (URLError, OSError, ValueError, json.JSONDecodeError):
        return None, "Não foi possível renovar a autorização do Gmail"


def gmail_authorization_status():
    """Checks the OAuth grant only; never returns tokens or sends a message."""
    if os.getenv("GMAIL_SENDER", GMAIL_SENDER).strip().lower() != GMAIL_SENDER:
        return False, "Confira GMAIL_SENDER: o remetente deve ser pontodecoletaadelinha@gmail.com"
    token, error = _gmail_access_token()
    if not token:
        return False, error
    return True, "Autorização do Gmail validada. Teste a recuperação de senha para confirmar o envio do e-mail."


def send_email(destination, subject, body, *, html_body=None):
    """Envia pela Gmail API; sucesso significa aceitação pela API, não entrega."""
    sender = os.getenv("GMAIL_SENDER", GMAIL_SENDER).strip().lower()
    if sender != GMAIL_SENDER:
        return False, f"Remetente Gmail inválido; configure {GMAIL_SENDER}"
    access_token, error = _gmail_access_token()
    if not access_token:
        return False, error
    mail = EmailMessage()
    mail["From"] = formataddr((GMAIL_DISPLAY_NAME, GMAIL_SENDER))
    mail["Reply-To"], mail["To"], mail["Subject"] = GMAIL_SENDER, destination, subject
    if html_body is not None:
        # Complete purpose-specific content: do not append the generic branding.
        mail.set_content(body)
        mail.add_alternative(html_body, subtype="html")
    else:
        mail.set_content(f"{body.rstrip()}\n\n{EMAIL_SIGNATURE}\n")
        safe_body = escape(body.rstrip()).replace('\n', '<br>')
        logo_markup = '<img src="cid:logo-adelinha" width="128" height="128" alt="Ponto de Coleta Adelinha" style="display:block;width:128px;height:128px;object-fit:contain;margin-bottom:20px">' if ORIGINAL_LOGO.is_file() else ''
        mail.add_alternative(f"""<!doctype html><html lang="pt-BR"><body style="margin:0;background:#eafafa;color:{GRAPHITE};font-family:Montserrat,Arial,sans-serif">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">
    <table role="presentation" width="100%" style="max-width:600px;background:#ffffff;border-top:6px solid {ORANGE};border-radius:16px"><tr><td style="padding:32px">
    {logo_markup}<h1 style="font-size:22px;margin:0 0 8px;color:{GRAPHITE}">Ponto de Coleta Adelinha</h1>
    <p style="color:{DEFAULT_THEME['link']};font-weight:bold">ME GUARDE — COMPROU? DEIXA COM A GENTE.</p>
    <h2 style="font-size:18px;color:{GRAPHITE}">{escape(subject)}</h2><p style="line-height:1.7;overflow-wrap:anywhere">{safe_body}</p>
    <hr style="border:0;border-top:2px solid {TEAL}"><p style="font-size:12px;color:{GRAPHITE}">Unidade Amor Infinito · {escape(EMAIL_SIGNATURE)}</p>
    </td></tr></table></td></tr></table></body></html>""", subtype="html")
        if ORIGINAL_LOGO.is_file():
            mail.get_payload()[-1].add_related(ORIGINAL_LOGO.read_bytes(), maintype="image", subtype="png", cid="<logo-adelinha>")
    raw = base64.urlsafe_b64encode(mail.as_bytes()).decode("ascii")
    request = Request("https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=json.dumps({"raw": raw}).encode("utf-8"),
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8") or "{}")
        message_id = result.get("id")
        if 200 <= response.status < 300 and message_id:
            return True, f"aceito pela Gmail API para processamento (id {message_id})"
        return False, "A Gmail API não confirmou que aceitou a mensagem"
    except HTTPError as error:
        return False, _json_http_error(error, "Gmail API")
    except (URLError, OSError, ValueError, json.JSONDecodeError):
        return False, "Não foi possível conectar à Gmail API"


def _provider_error(error):
    if isinstance(error, HTTPError):
        return _json_http_error(error, "WhatsApp")
    if isinstance(error, URLError):
        return "Não foi possível conectar ao serviço do WhatsApp"
    return f"Falha no provedor: {type(error).__name__}"


def _send_whatsapp(destination, code):
    token = os.getenv("WHATSAPP_ACCESS_TOKEN")
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
    template_name = os.getenv("WHATSAPP_TEMPLATE_NAME")
    version = os.getenv("WHATSAPP_GRAPH_VERSION")
    if not token or not phone_id or not template_name or not version:
        return False, "WhatsApp não configurado: confira token, número, versão da API e modelo aprovado"
    try:
        destination = normalize_whatsapp_destination(destination)
    except ValueError as error:
        return False, str(error)
    payload = {"messaging_product": "whatsapp", "to": destination, "type": "template", "template": {
        "name": template_name, "language": {"code": os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "pt_BR")},
        "components": [{"type": "body", "parameters": [{"type": "text", "text": code}]}],
    }}
    request = Request(f"https://graph.facebook.com/{version}/{phone_id}/messages",
        data=json.dumps(payload).encode("utf-8"), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
            message_id = ((data.get("messages") or [{}])[0]).get("id")
            if 200 <= response.status < 300 and message_id:
                return True, f"aceito pelo WhatsApp para processamento (id {message_id})"
            return False, "O WhatsApp não confirmou que aceitou a mensagem"
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return False, _provider_error(error)


def confirmation_email(code):
    """Exact confirmation copy in both alternatives, retaining leading zeroes."""
    subject = "Código de confirmação — Ponto de Coleta Adelinha"
    body = (f"Ponto de Coleta Adelinha\n\nCódigo de confirmação: {code}\n\n"
            "Ele expira em 10 minutos.\n\nPONTO DE COLETA ADELINHA\n"
            "ME GUARDE — COMPROU? DEIXA COM A GENTE.\n")
    html_body = f"""<!doctype html><html lang="pt-BR"><head><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#ffffff;color:{GRAPHITE};font-family:Arial,sans-serif">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:20px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px"><tr><td style="padding:12px;line-height:1.6;overflow-wrap:anywhere">
<p style="font-size:22px;margin:0 0 24px"><strong>Ponto de Coleta Adelinha</strong></p>
<p style="font-size:18px;margin:0 0 20px">Código de confirmação: <strong style="font-size:28px;white-space:nowrap">{escape(str(code))}</strong></p>
<p style="font-size:16px;margin:0 0 28px">Ele expira em <strong>10 minutos</strong>.</p>
<p style="font-size:14px;margin:0"><strong>PONTO DE COLETA ADELINHA<br>ME GUARDE — COMPROU? DEIXA COM A GENTE.</strong></p>
</td></tr></table></td></tr></table></body></html>"""
    return subject, body, html_body


def send_code(channel, destination, code):
    if channel == "email":
        subject, body, html_body = confirmation_email(code)
        return send_email(destination, subject, body, html_body=html_body)
    if channel == "whatsapp":
        return _send_whatsapp(destination, code)
    return False, "Canal inválido"
