import plan_service
import plan_routes
import os
import secrets
import hashlib
import base64
import math
import re
import json
import html
from email.utils import parseaddr
from decimal import Decimal
from io import BytesIO
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_file, session, url_for, Response, render_template_string
from markupsafe import escape
import qrcode
from PIL import Image, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash

from explanatory_cards import DEFAULT_CARDS, optimized_versions
from store_logos import STORE_LOGOS
from branding import BRAND_VERSION, ORIGINAL_LOGO, DEFAULT_THEME, default_stylesheet, theme_variables

from core import (
    TZ, calculate_discount, calculate_fees, classify_package, package_arrival_message,
    pix_payload, valid_cpf, validate_surcharge,
)
from database import DatabaseConnection, DatabaseIntegrityError, is_postgres
from receipt_pdf import build_receipt_pdf
from notifications import notification_config_status, send_code
from contract import CONTRACT_TEXT, CONTRACT_VERSION, CONTRACT_LABEL
from privacy_policy import PRIVACY_UPDATED, PRIVACY_CONTACT as PRIVACY_EMAIL, PRIVACY_REVIEW_PENDING
from client_compliance import FIELDS as CONTRACT_FIELDS, compliance, effective_record, signature_data, revision_id
from client_regularization import install_regularization
import client_actions
import blog_tools
from contract_pdf import build_contract_pdf, build_current_contract_pdf
from google_login import install_google_login

ROOT = Path(__file__).parent
TERMS_VERSION = CONTRACT_VERSION
IMAGE_UPLOAD_MAX_BYTES = 900 * 1024
BANNER_MAX_BYTES = IMAGE_UPLOAD_MAX_BYTES
PIX_KEY = "38145273000105"
PIX_RECEIVER_NAME = "AMOR INFINITO MARKETING E SOLUCOES EMPRESARIAIS"
# O CEP não é exibido aqui porque não foi confirmado para o endereço de retirada.
PICKUP_ADDRESS = "RUA AFONSO BORGES DE FREITAS, 795, JARDIM ADELINHA, FRANCA – SP"
PICKUP_CEP = os.environ.get("PICKUP_CEP", "14406-848")
PRIVACY_CONTACT = os.environ.get("PRIVACY_CONTACT", "(16) 98800-4966")
ADDRESS = PICKUP_ADDRESS  # compatibilidade com comprovantes e integrações existentes
CLIENT_COLUMNS = "id,public_id,name,cpf,phone,email,terms_version,terms_text,signature,accepted_at,active,session_version,residential_cep,residential_street,residential_number,residential_district,residential_city,residential_state,residential_complement,legacy_public_id,contract_public_id,document_brand_version,document_logo_present,terms_display_label"
PACKAGE_COLUMNS = "id,public_id,client_id,tracking,width,height,length,weight,shelf,category,base_price,status,created_at,notified_at,picked_at"
PICKUP_COLUMNS = "id,receiver_name,receiver_document,document_type,signature,staff,picked_at,base_total,late_total,total_paid,payment_method,payment_confirmed,payment_confirmed_at,payment_confirmed_by,discount_type,discount_input,discount_amount,original_total,final_total,discount_applied_by,surcharge_amount,surcharge_reason,surcharge_applied_by,pix_key_snapshot,pix_receiver_snapshot,pix_key_type_snapshot,document_brand_version,document_logo_present"
PICKUP_PACKAGE_COLUMNS = "pickup_id,package_id,base_amount,late_amount,package_public_id,client_id,client_name,client_public_id,tracking_code,package_category,notified_at,service_amount,late_days,total_amount"
PACKAGE_COLUMNS_P = ",".join(f"p.{column}" for column in PACKAGE_COLUMNS.split(","))
PICKUP_COLUMNS_PU = ",".join(f"pu.{column}" for column in PICKUP_COLUMNS.split(","))


def validated_image_type(data, maximum_bytes):
    """Valida o conteúdo real sem confiar no nome ou Content-Type enviado."""
    if not data or len(data) > maximum_bytes:
        return None
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
            return {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(image.format)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        return None


def validate_pix_settings(receiver, key_type, key):
    import unicodedata
    import uuid
    normalized = unicodedata.normalize("NFKD", receiver).encode("ascii", "ignore").decode().strip()
    if not normalized or len(receiver) > 180 or any(ord(c) < 32 for c in receiver):
        raise ValueError("Informe um nome de titular válido com até 180 caracteres.")
    if key_type in {"CPF", "CNPJ"}:
        if not re.fullmatch(r"[0-9./\-]+", key):
            raise ValueError("Informe uma chave CPF ou CNPJ válida.")
        key = digits(key)
        if key_type == "CPF" and not valid_cpf(key):
            raise ValueError("CPF inválido.")
        if key_type == "CNPJ":
            if len(key) != 14 or len(set(key)) == 1:
                raise ValueError("CNPJ inválido.")
            for length in (12, 13):
                weights = ([5,4,3,2,9,8,7,6,5,4,3,2] if length == 12 else [6,5,4,3,2,9,8,7,6,5,4,3,2])
                remainder = sum(int(n)*w for n,w in zip(key[:length], weights)) % 11
                if int(key[length]) != (0 if remainder < 2 else 11-remainder):
                    raise ValueError("CNPJ inválido.")
    elif key_type == "E-mail":
        if not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~\-]+@[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,}", key) or len(key) > 77:
            raise ValueError("Informe um e-mail Pix válido com até 77 caracteres.")
        key = key.lower()
    elif key_type == "Telefone":
        if not re.fullmatch(r"\+[1-9][0-9]{7,14}", key):
            raise ValueError("Informe o telefone com +, código do país e DDD, sem espaços.")
    elif key_type == "Aleatória":
        try:
            key = str(uuid.UUID(key))
        except ValueError:
            raise ValueError("Informe uma chave aleatória UUID válida.") from None
    else:
        raise ValueError("Selecione um tipo de chave Pix válido.")
    return key


def format_blog_body(value):
    """Converte a formatação simples do editor em HTML seguro e legível."""
    lines = html.unescape(value or "").splitlines()
    output, paragraph, list_items = [], [], []

    def flush_paragraph():
        if paragraph:
            output.append(f"<p>{'<br>'.join(str(escape(line)) for line in paragraph)}</p>")
            paragraph.clear()

    def flush_list():
        if list_items:
            output.append("<ul>" + "".join(f"<li>{escape(item)}</li>" for item in list_items) + "</ul>")
            list_items.clear()

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            flush_paragraph(); flush_list()
        elif line.startswith("### "):
            flush_paragraph(); flush_list(); output.append(f"<h3>{escape(line[4:])}</h3>")
        elif line.startswith("## "):
            flush_paragraph(); flush_list(); output.append(f"<h2>{escape(line[3:])}</h2>")
        elif line.startswith("- "):
            flush_paragraph(); list_items.append(line[2:].strip())
        else:
            flush_list(); paragraph.append(line)
    flush_paragraph(); flush_list()
    return "".join(output)


def terms_text():
    return CONTRACT_TEXT.replace(
        "inscrita no CNPJ e situada no endereço e CEP informados nas configurações administrativas e exibidos neste documento",
        f"inscrita no CNPJ 38.145.273/0001-05 e situada na Rua Afonso Borges de Freitas, nº 795, Jardim Adelinha, Franca – SP, CEP {PICKUP_CEP}",
    ).replace("canal de privacidade exibido no documento", f"canal de privacidade {PRIVACY_CONTACT}")


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", secrets.token_hex(32)),
        DATABASE=os.environ.get("DATABASE_URL") or os.environ.get("DATABASE", str(ROOT / "data" / "adelinha.db")),
        STAFF_USER=os.environ.get("STAFF_USER", "admin"),
        STAFF_NAME=os.environ.get("STAFF_NAME", "Administrador"),
        STAFF_PASSWORD_HASH=os.environ.get("STAFF_PASSWORD_HASH", generate_password_hash("adelinha-teste")),
        GOOGLE_LOGIN_ENABLED=os.environ.get('GOOGLE_LOGIN_ENABLED', '0'),
        GOOGLE_LOGIN_CLIENT_ID=os.environ.get('GOOGLE_LOGIN_CLIENT_ID', ''),
        GOOGLE_LOGIN_CLIENT_SECRET=os.environ.get('GOOGLE_LOGIN_CLIENT_SECRET', ''),
        GOOGLE_LOGIN_REDIRECT_URI=os.environ.get('GOOGLE_LOGIN_REDIRECT_URI', ''),
        FASTFLOW_ENABLED=os.environ.get('FASTFLOW_ENABLED','0'),
        FASTFLOW_API_KEY=os.environ.get('FASTFLOW_API_KEY',''),
        FASTFLOW_WEBHOOK_SECRET=os.environ.get('FASTFLOW_WEBHOOK_SECRET',''),
        FASTFLOW_PUBLIC_ORIGIN=os.environ.get('FASTFLOW_PUBLIC_ORIGIN',''),
        FASTFLOW_CARD_ENABLED=os.environ.get('FASTFLOW_CARD_ENABLED','0'),
        FASTFLOW_CARD_PCI_READY=os.environ.get('FASTFLOW_CARD_PCI_READY','0'),
        ME_GUARDE_MAIS_LOGO_PATH=os.environ.get('ME_GUARDE_MAIS_LOGO_PATH',''),
        CURRENT_DOCUMENT_BRAND=BRAND_VERSION,
        CURRENT_DOCUMENT_LOGO=int(ORIGINAL_LOGO.is_file()),
        PASSWORD_LINK_MINUTES=30,
        LOGIN_LIMIT=5,
        LOGIN_WINDOW_MINUTES=15,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
    )
    if test_config:
        app.config.update(test_config)

    def current_contract():
        return client_actions.current_contract(db(),(TERMS_VERSION,CONTRACT_LABEL,terms_text()))

    @app.template_filter("package_arrival_message")
    def package_arrival_message_filter(package):
        return package_arrival_message(
            package["client_name"], package["public_id"],
            package["tracking"], package["base_price"],
        )

    def db():
        if "db" not in request.environ:
            connection = DatabaseConnection(app.config["DATABASE"])
            request.environ["db"] = connection
        return request.environ["db"]

    @app.teardown_request
    def close_db(_error=None):
        connection = request.environ.pop("db", None)
        if connection:
            connection.close()

    def now():
        return datetime.now(TZ)

    def mask_contact(channel, destination):
        if channel == "email" and "@" in destination:
            local, domain = destination.split("@", 1)
            return f"{local[:2]}***@{domain}"
        value = digits(destination)
        return f"***{value[-4:]}" if value else "contato cadastrado"

    def current_staff():
        if session.get("staff_user_id"):
            return db().execute("SELECT id,public_id,name,email,role,is_primary,active,can_settings,can_users,can_clients,can_packages,can_pickups,session_version,deleted_at FROM staff_users WHERE id=?", (session["staff_user_id"],)).fetchone()
        return None

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            staff = current_staff()
            if session.get("staff_user_id") and (not staff or not staff["active"] or staff["deleted_at"] or staff["session_version"] != session.get("staff_session_version")):
                session.clear()
            if not session.get("staff"):
                return redirect(url_for("login", next=request.path))
            return view(*args, **kwargs)
        return wrapped

    def admin_required(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            staff = current_staff()
            if session.get("staff_user_id") and not (staff and staff["can_settings"]):
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    def primary_required(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            staff = current_staff()
            if session.get("staff_user_id") and not (staff and staff["role"] == "administrador" and staff["can_users"]):
                abort(403)
            return view(*args, **kwargs)
        return wrapped

    def client_login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            client = db().execute("SELECT active,session_version FROM clients WHERE id=?", (session.get("client_id"),)).fetchone() if session.get("client_id") else None
            if not client or not client["active"] or client["session_version"] != session.get("client_session_version"):
                session.clear()
                return redirect(url_for("client_login"))
            return view(*args, **kwargs)
        return wrapped

    @app.before_request
    def csrf_protection():
        if request.endpoint == "plans.webhook":
            return None  # Raw-body HMAC replaces browser CSRF only on this callback.
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_urlsafe(32)
        if request.method == "POST" and not secrets.compare_digest(request.form.get("csrf_token", ""), session["csrf_token"]):
            abort(400, "Token CSRF inválido. Atualize a página e tente novamente.")
        permissions = {
            "client_search": "can_clients", "staff_client": "can_clients",
            "edit_client": "can_clients", "admin_client_password": "can_clients",
            "remove_client": "can_clients", "add_package": "can_packages",
            "edit_package": "can_packages", "delete_package": "can_packages",
            "notify": "can_packages", "package_detail": "can_packages",
            "pickup_summary": "can_pickups", "pickup": "can_pickups", "receipt": "can_pickups",
        }
        required = permissions.get(request.endpoint)
        if required and session.get("staff_user_id"):
            staff = current_staff()
            if not staff or not staff[required]:
                abort(403)
        if request.method == "GET" and request.endpoint in {"index", "faq", "blog", "blog_post", "terms"}:
            visitor = session.setdefault("analytics_visitor", secrets.token_urlsafe(12))
            agent = request.headers.get("User-Agent", "")[:300]
            device = "celular" if re.search(r"Mobile|Android|iPhone", agent, re.I) else "computador"
            referrer = request.referrer or ""
            origin = "direto"
            if referrer:
                origin = re.sub(r"^https?://", "", referrer).split("/", 1)[0][:120] or "desconhecida"
            db().execute("INSERT INTO analytics_events(visitor_id,event_type,path,origin,device,created_at) VALUES(?,?,?,?,?,?)",
                (visitor, "pageview", request.path[:300], origin, device, now().isoformat()))
            db().commit()

    @app.after_request
    def protect_session_pages(response):
        # A página inicial muda conforme a sessão; dados autenticados nunca devem
        # ser reaproveitados por caches compartilhados da Oracle ou do navegador.
        if request.endpoint in {"index", "terms", "current_contract_pdf", "client_login", "client_login_post", "privacy"} or (request.endpoint or "").startswith("google_login.") or ((session.get("staff") or session.get("client_id")) and request.endpoint not in {"header_identity", "main_service_banner", "explanatory_card_image"}):
            response.headers["Cache-Control"] = "private, no-store, max-age=0"
            response.headers["Vary"] = "Cookie"
        if (request.endpoint or "").startswith("google_login."):
            response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.context_processor
    def shared_values():
        client_name = None
        if session.get("client_id"):
            row = db().execute("SELECT name FROM clients WHERE id=?", (session["client_id"],)).fetchone()
            client_name = row["name"] if row else None
        staff = current_staff() if session.get("staff_user_id") else None
        setting = db().execute("SELECT active_theme_id,CASE WHEN header_image IS NOT NULL THEN 1 ELSE 0 END header_customized FROM settings WHERE id=1").fetchone()
        theme = db().execute("SELECT colors FROM site_themes WHERE id=?", (setting["active_theme_id"],)).fetchone() if setting and setting["active_theme_id"] else None
        try: theme_colors = json.loads(theme["colors"]) if theme else {}
        except (TypeError, json.JSONDecodeError): theme_colors = {}
        contract_version,contract_label,contract_text=current_contract()
        return {"csrf_token": session.get("csrf_token"), "address": PICKUP_ADDRESS,
                "pickup_address": PICKUP_ADDRESS, "pix_receiver_name_full": db().execute("SELECT pix_receiver_name,pix_key,pix_key_type FROM settings WHERE id=1").fetchone()["pix_receiver_name"],
                "logged_name": staff["name"] if staff else (app.config["STAFF_NAME"] if session.get("staff") else client_name),
                "pickup_cep": PICKUP_CEP, "theme_colors": theme_colors, "theme_variables": theme_variables(theme_colors),
                "current_contract_label": contract_label, "current_terms_version": contract_version, "current_terms_digest": hashlib.sha256(contract_text.encode()).hexdigest(),
                "image_upload_max_bytes": IMAGE_UPLOAD_MAX_BYTES, "store_logos": STORE_LOGOS,
                "explanatory_cards": db().execute("SELECT id,title,alt_text,revision,width,height FROM explanatory_cards ORDER BY id").fetchall(),
                "original_logo_available": ORIGINAL_LOGO.is_file(), "header_customized": bool(setting and setting["header_customized"]), "staff_account": staff,
                "staff_legacy": session.get("staff_legacy")}

    @app.template_filter("local_datetime")
    def local_datetime(value):
        if not value:
            return "Não registrado"
        return datetime.fromisoformat(value).astimezone(TZ).strftime("%d/%m/%Y %H:%M")

    def audit(action, entity_type, entity_id, details=""):
        db().execute("INSERT INTO audit_logs(actor,action,entity_type,entity_id,details,created_at) VALUES(?,?,?,?,?,?)",
            (session.get("staff", "sistema"), action, entity_type, entity_id, details, now().isoformat()))

    def package_form_data():
        return {key: request.form.get(key, "") for key in ("client_id", "client_label", "tracking", "shelf", "weight", "width", "height", "length")}

    def pagination(default_size=25):
        try:
            page = max(1, int(request.args.get("page", 1)))
        except ValueError:
            page = 1
        return page, default_size, (page - 1) * default_size

    def residential_address_form():
        return {key: request.form.get(key, "").strip() for key in
                ("residential_cep", "residential_street", "residential_number",
                 "residential_district", "residential_city", "residential_state",
                 "residential_complement")}

    def valid_residential_address(data):
        required = ("residential_street", "residential_number", "residential_district", "residential_city")
        return (len(digits(data["residential_cep"])) == 8
                and all(data[field] for field in required)
                and bool(re.fullmatch(r"[A-Za-z]{2}", data["residential_state"])))

    def valid_email(value):
        """E-mail é opcional, mas, quando informado, deve ter formato completo."""
        if not value:
            return True
        parsed = parseaddr(value)[1]
        return parsed == value and len(value) <= 254 and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))

    def validate_package_form(exclude_package_id=None):
        data = package_form_data()
        tracking, shelf = data["tracking"].strip(), data["shelf"].strip()
        if not tracking or not shelf or not re.fullmatch(r"[A-Za-z0-9]+", tracking) or not re.fullmatch(r"[A-Za-z0-9]+", shelf):
            raise ValueError("Rastreio e prateleira são obrigatórios e devem conter somente letras e números.")
        try:
            client_id = int(data["client_id"])
            numbers = [float(data[key].replace(",", ".")) for key in ("width", "height", "length", "weight")]
        except (ValueError, TypeError):
            raise ValueError("Selecione um cliente válido e informe peso e medidas numéricos.")
        if not all(math.isfinite(value) and value > 0 for value in numbers):
            raise ValueError("Peso e medidas devem ser números maiores que zero.")
        client = db().execute("SELECT id,name,public_id FROM clients WHERE id=? AND active=1", (client_id,)).fetchone()
        if not client:
            raise ValueError("Selecione um cliente válido e ativo.")
        duplicate = db().execute("SELECT id FROM packages WHERE UPPER(tracking)=UPPER(?) AND id!=? LIMIT 1",
                                 (tracking, exclude_package_id or 0)).fetchone()
        if duplicate:
            raise ValueError("Este código de rastreio já está cadastrado. Confira o pacote existente.")
        width, height, length, weight = numbers
        category, price = classify_package(width, height, length, weight)
        return data, client, (width, height, length, weight), category, price

    def login_blocked(kind, identifier):
        cutoff = (now() - timedelta(minutes=app.config["LOGIN_WINDOW_MINUTES"])).isoformat()
        count = db().execute("SELECT COUNT(*) AS total FROM login_attempts WHERE kind=? AND identifier=? AND succeeded=0 AND attempted_at>=?", (kind, identifier, cutoff)).fetchone()["total"]
        return count >= app.config["LOGIN_LIMIT"]

    def record_login(kind, identifier, succeeded):
        db().execute("INSERT INTO login_attempts(kind,identifier,succeeded,attempted_at) VALUES(?,?,?,?)", (kind, identifier, int(succeeded), now().isoformat()))
        if succeeded:
            db().execute("DELETE FROM login_attempts WHERE kind=? AND identifier=? AND succeeded=0", (kind, identifier))
        db().commit()

    def find_clients(query):
        """Usa a mesma fonte e os mesmos critérios em todas as buscas do painel."""
        query = query.strip()
        if not query:
            return []
        like = f"%{query}%"
        phone = digits(query) if re.fullmatch(r"[0-9\s()+.\-]+", query) else ""
        phone_like = f"%{phone}%" if phone else like
        return db().execute(
            """SELECT id, public_id, name, phone FROM clients
               WHERE LOWER(name) LIKE LOWER(?)
                  OR LOWER(public_id) LIKE LOWER(?)
                  OR LOWER(legacy_public_id) LIKE LOWER(?)
                  OR phone LIKE ?
               ORDER BY name LIMIT 30""",
            (like, like, like, phone_like),
        ).fetchall()

    def configured_banners():
        setting = db().execute("SELECT * FROM settings WHERE id=1").fetchone()
        banners = [dict(row) for row in db().execute(
            "SELECT id,alt_text,position,updated_at,active FROM site_banners ORDER BY position,id").fetchall()]
        banners.append({"id": 0, "alt_text": setting["main_banner_alt"],
                        "position": setting["main_banner_position"], "active": setting["main_banner_active"],
                        "updated_at": setting["main_banner_updated_at"], "main": True})
        for banner in banners:
            banner["src"] = (url_for("main_service_banner") if banner["id"] == 0 else
                             url_for("banner_image", banner_id=banner["id"]))
        return sorted(banners, key=lambda banner: banner["position"])

    @app.get("/")
    def index():
        banners = [banner for banner in configured_banners() if banner["active"]]
        photos = db().execute("SELECT id,kind,alt_text,position,updated_at FROM site_photos ORDER BY position,id LIMIT 3").fetchall()
        show_registration = not (session.get("client_id") or session.get("staff")) or bool(session.get("staff") and request.args.get("cadastro") == "1")
        return render_template("index.html", banners=banners, photos=photos, show_registration=show_registration)

    @app.get("/identidade/banner-principal")
    def main_service_banner():
        setting = db().execute(
            "SELECT main_banner_image,main_banner_mime,main_banner_updated_at FROM settings WHERE id=1"
        ).fetchone()
        if setting and setting["main_banner_image"]:
            return Response(
                bytes(setting["main_banner_image"]),
                mimetype=setting["main_banner_mime"],
                headers={"Cache-Control": "public,max-age=3600"},
            )
        return redirect(url_for("static", filename=f"brand/{BRAND_VERSION}/me-guarde-banner.webp", v=BRAND_VERSION))

    @app.post("/metricas/evento")
    def analytics_event():
        event = request.form.get("event", "")
        if event not in {"cadastro", "area_cliente", "whatsapp"}:
            abort(400)
        visitor = session.setdefault("analytics_visitor", secrets.token_urlsafe(12))
        db().execute("INSERT INTO analytics_events(visitor_id,event_type,path,origin,device,created_at) VALUES(?,?,?,?,?,?)",
            (visitor, event, request.form.get("path", "")[:300], "interface", "não registrado", now().isoformat()))
        db().commit()
        return ("", 204)

    @app.get("/painel/metricas")
    @admin_required
    def metrics_dashboard():
        period = request.args.get("period", "7")
        days = 1 if period == "today" else (30 if period == "30" else 7)
        start = (now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "custom":
            try:
                start = datetime.fromisoformat(request.args.get("start", "")).replace(tzinfo=TZ)
                end = datetime.fromisoformat(request.args.get("end", "")).replace(tzinfo=TZ) + timedelta(days=1)
            except ValueError:
                abort(400, "Período inválido")
        else:
            end = now() + timedelta(seconds=1)
        start_iso, end_iso = start.isoformat(), end.isoformat()
        visits = db().execute("SELECT COUNT(id) visits,COUNT(DISTINCT visitor_id) visitors,MIN(created_at) first_at FROM analytics_events WHERE event_type='pageview' AND created_at>=? AND created_at<?", (start_iso,end_iso)).fetchone()
        pages = db().execute("SELECT path,COUNT(id) total FROM analytics_events WHERE event_type='pageview' AND created_at>=? AND created_at<? GROUP BY path ORDER BY total DESC LIMIT 10", (start_iso,end_iso)).fetchall()
        devices = db().execute("SELECT device,COUNT(id) total FROM analytics_events WHERE event_type='pageview' AND created_at>=? AND created_at<? GROUP BY device", (start_iso,end_iso)).fetchall()
        origins = db().execute("SELECT origin,COUNT(id) total FROM analytics_events WHERE event_type='pageview' AND created_at>=? AND created_at<? GROUP BY origin ORDER BY total DESC LIMIT 10", (start_iso,end_iso)).fetchall()
        clicks = db().execute("SELECT event_type,COUNT(id) total FROM analytics_events WHERE event_type!='pageview' AND created_at>=? AND created_at<? GROUP BY event_type", (start_iso,end_iso)).fetchall()
        operational = {
            "new_clients": db().execute("SELECT COUNT(id) total FROM clients WHERE accepted_at>=? AND accepted_at<?",(start_iso,end_iso)).fetchone()["total"],
            "received": db().execute("SELECT COUNT(id) total FROM packages WHERE created_at>=? AND created_at<?",(start_iso,end_iso)).fetchone()["total"],
            "waiting": db().execute("SELECT COUNT(id) total FROM packages WHERE status='aguardando_retirada'").fetchone()["total"],
            "picked": db().execute("SELECT COUNT(id) total FROM packages WHERE status='retirado' AND picked_at>=? AND picked_at<?",(start_iso,end_iso)).fetchone()["total"],
            "late": 0,
        }
        notified=db().execute("SELECT notified_at FROM packages WHERE status='aguardando_retirada' AND notified_at IS NOT NULL").fetchall()
        operational["late"]=sum(1 for item in notified if (now().date()-datetime.fromisoformat(item["notified_at"]).astimezone(TZ).date()).days+1 >= 5)
        money = db().execute("SELECT COALESCE(SUM(final_total),0) received,COALESCE(SUM(discount_amount),0) discounts,COALESCE(SUM(surcharge_amount),0) surcharges,COALESCE(SUM(CASE WHEN payment_method='Pix' THEN final_total ELSE 0 END),0) pix,COALESCE(SUM(CASE WHEN payment_method='Dinheiro' THEN final_total ELSE 0 END),0) cash FROM pickups WHERE picked_at>=? AND picked_at<?",(start_iso,end_iso)).fetchone()
        return render_template("metrics.html", visits=visits, pages=pages, devices=devices, origins=origins, clicks=clicks,
            operational=operational, money=money, period=period, start=start.date(), end=(end-timedelta(days=1)).date())

    @app.get("/painel/configuracoes")
    @login_required
    def settings_hub():
        staff = current_staff()
        if session.get("staff_user_id") and not (staff and (staff["can_settings"] or staff["can_users"])):
            abort(403)
        return render_template("settings_hub.html")

    @app.get("/duvidas-frequentes")
    def faq():
        return render_template("faq.html")

    @app.get("/identidade/paleta.css")
    def brand_stylesheet():
        return Response(default_stylesheet(), mimetype="text/css", headers={"Cache-Control": "public,max-age=3600"})

    @app.get("/identidade/cabecalho")
    def header_identity():
        setting = db().execute("SELECT header_image,header_image_mime FROM settings WHERE id=1").fetchone()
        if setting and setting["header_image"]:
            return Response(bytes(setting["header_image"]), mimetype=setting["header_image_mime"],
                            headers={"Cache-Control": "no-store, max-age=0"})
        if ORIGINAL_LOGO.is_file():
            return send_file(ORIGINAL_LOGO, mimetype="image/png", max_age=0)
        response = redirect(url_for("static", filename="logo-adelinha.svg", v="default-1"))
        response.headers["Cache-Control"] = "no-store, max-age=0"
        return response

    @app.route("/painel/configuracoes/banner-principal", methods=["GET", "POST"], endpoint="main_banner_settings")
    @app.route("/painel/configuracoes/identidade", methods=["GET", "POST"])
    @admin_required
    def visual_identity():
        main_banner_page = request.endpoint == "main_banner_settings"
        staff = current_staff()
        if (main_banner_page or request.form.get("action") in {"main_banner", "restore_main_banner"}) and staff and staff["role"] != "administrador":
            abort(403)
        if main_banner_page and request.method == "GET":
            return redirect(url_for("banner_settings"))
        template = "banner_settings.html" if main_banner_page else "visual_identity.html"
        if request.method == "POST":
            action = request.form.get("action", "main_banner" if main_banner_page else "header")
            if main_banner_page and (action not in {"main_banner", "restore_main_banner"} or request.form.get("restore")):
                abort(400)
            if request.form.get("restore") == "yes":
                db().execute("UPDATE settings SET header_image=NULL,header_image_mime=NULL WHERE id=1")
                audit("restaurar_identidade_visual", "configuracao", 1)
            elif action == "restore_main_banner":
                db().execute("UPDATE settings SET main_banner_image=NULL,main_banner_mime=NULL,main_banner_updated_at=? WHERE id=1", (now().isoformat(),))
                audit("restaurar_banner_principal", "configuracao", 1)
            elif action == "main_banner":
                upload = request.files.get("main_banner_image")
                data = upload.read(BANNER_MAX_BYTES + 1) if upload else b""
                mime = validated_image_type(data, BANNER_MAX_BYTES)
                if not data or len(data) > BANNER_MAX_BYTES or not mime:
                    flash("Envie um banner PNG, JPG ou WebP de até 900 KB. A imagem anterior foi preservada.", "error")
                    return render_template("banner_settings.html", banners=configured_banners()), 400
                db().execute(
                    "UPDATE settings SET main_banner_image=?,main_banner_mime=?,main_banner_updated_at=? WHERE id=1",
                    (data, mime, now().isoformat()),
                )
                audit("alterar_banner_principal", "configuracao", 1)
            else:
                upload = request.files.get("image")
                data = upload.read(IMAGE_UPLOAD_MAX_BYTES + 1) if upload else b""
                mime = validated_image_type(data, IMAGE_UPLOAD_MAX_BYTES)
                if not data or len(data) > IMAGE_UPLOAD_MAX_BYTES or not mime:
                    flash("Envie uma imagem PNG, JPG ou WebP de até 900 KB. A imagem anterior foi preservada.", "error")
                    return render_template("visual_identity.html"), 400
                db().execute("UPDATE settings SET header_image=?,header_image_mime=? WHERE id=1", (data, mime))
                audit("alterar_identidade_visual", "configuracao", 1)
            db().commit()
            flash("Identidade visual alterada com sucesso!", "success")
            return redirect(url_for("banner_settings" if main_banner_page or action in {"main_banner", "restore_main_banner"} else "visual_identity"))
        setting = db().execute("SELECT main_banner_updated_at FROM settings WHERE id=1").fetchone()
        return render_template(template, main_banner_version=setting["main_banner_updated_at"] if setting else "default")

    @app.get("/banners/<int:banner_id>/imagem")
    def banner_image(banner_id):
        banner = db().execute("SELECT image,mime_type,updated_at FROM site_banners WHERE id=?", (banner_id,)).fetchone()
        if not banner: abort(404)
        return Response(bytes(banner["image"]), mimetype=banner["mime_type"], headers={"Cache-Control": "public,max-age=3600"})

    @app.route("/painel/configuracoes/banners", methods=["GET", "POST"])
    @admin_required
    def banner_settings():
        staff = current_staff()
        if staff and staff["role"] != "administrador":
            abort(403)
        banners = configured_banners()
        if request.method == "POST":
            try:
                banner_id = int(request.form.get("banner_id", "-1"))
                position = int(request.form.get("position", "1"))
            except ValueError:
                abort(400)
            existing = next((b for b in banners if b["id"] == banner_id), None)
            alt_text = request.form.get("alt_text", "").strip()
            active = int(request.form.get("active") == "yes")
            upload = request.files.get("image")
            has_upload = bool(upload and upload.filename)
            data = upload.read(BANNER_MAX_BYTES + 1) if has_upload else b""
            mime = validated_image_type(data, BANNER_MAX_BYTES) if has_upload else None
            error = None
            if has_upload and not mime:
                error = "Imagem inválida. Envie PNG, JPG ou WebP de até 900 KB. A imagem anterior foi preservada."
            elif not alt_text or len(alt_text) > 180 or not 1 <= position <= max(3, len(banners)):
                error = "Informe uma descrição de até 180 caracteres e uma ordem válida."
            elif not existing and (banner_id != -1 or len(banners) >= 3 or not has_upload):
                error = "Selecione um banner existente ou envie uma imagem para uma das três posições."
            elif active and sum(b["active"] for b in banners if b["id"] != banner_id) >= 3:
                error = "Ative no máximo três banners. Desative outro antes de ativar este."
            if error:
                flash(error, "error")
                return render_template("banner_settings.html", banners=banners), 400
            # Troca de posições em uma transação, sem violar a chave única antiga.
            db().begin_write()
            conflict = next((b for b in banners if b["position"] == position and b["id"] != banner_id), None)
            old_position = existing["position"] if existing else next(i for i in range(1, 5) if all(b["position"] != i for b in banners))
            if existing and banner_id != 0:
                db().execute("UPDATE site_banners SET position=0 WHERE id=?", (banner_id,))
            if conflict:
                if conflict["id"] == 0:
                    db().execute("UPDATE settings SET main_banner_position=? WHERE id=1", (old_position,))
                else:
                    db().execute("UPDATE site_banners SET position=? WHERE id=?", (old_position, conflict["id"]))
            timestamp = now().isoformat()
            if banner_id == 0:
                db().execute("UPDATE settings SET main_banner_alt=?,main_banner_position=?,main_banner_active=?,main_banner_updated_at=? WHERE id=1",
                             (alt_text, position, active, timestamp))
                if has_upload:
                    db().execute("UPDATE settings SET main_banner_image=?,main_banner_mime=? WHERE id=1", (data, mime))
            elif existing:
                db().execute("UPDATE site_banners SET alt_text=?,position=?,active=?,updated_at=? WHERE id=?",
                             (alt_text, position, active, timestamp, banner_id))
                if has_upload:
                    db().execute("UPDATE site_banners SET image=?,mime_type=? WHERE id=?", (data, mime, banner_id))
            else:
                db().execute("INSERT INTO site_banners(image,mime_type,alt_text,position,active,updated_at) VALUES(?,?,?,?,?,?)",
                             (data, mime, alt_text, position, active, timestamp))
            audit("salvar_banner", "banner", banner_id, f"posição {position}; ativo {active}")
            db().commit()
            flash("Banner salvo.", "success")
            return redirect(url_for("banner_settings"))
        return render_template("banner_settings.html", banners=banners)

    @app.get("/fotos/<int:photo_id>/imagem")
    def site_photo_image(photo_id):
        photo=db().execute("SELECT image,mime_type FROM site_photos WHERE id=?",(photo_id,)).fetchone()
        if not photo: abort(404)
        return Response(bytes(photo["image"]),mimetype=photo["mime_type"],headers={"Cache-Control":"public,max-age=3600"})

    @app.route("/painel/configuracoes/fotos", methods=["GET", "POST"])
    @admin_required
    def site_photos_settings():
        if request.method == "POST":
            try: photo_id=int(request.form.get("photo_id","0")); position=int(request.form.get("position","1"))
            except ValueError: photo_id,position=0,0
            if request.form.get("action") == "remove":
                db().execute("DELETE FROM site_photos WHERE id=?",(photo_id,)); audit("remover_foto_local","foto",photo_id); db().commit(); flash("Foto removida.","success"); return redirect(url_for("site_photos_settings"))
            alt=request.form.get("alt_text","").strip(); kind=request.form.get("kind","")
            upload = request.files.get("image")
            has_upload = bool(upload and upload.filename)
            data = upload.read(IMAGE_UPLOAD_MAX_BYTES + 1) if has_upload else b""
            mime = validated_image_type(data, IMAGE_UPLOAD_MAX_BYTES) if has_upload else None
            if has_upload and not mime:
                flash("Envie uma foto PNG, JPG ou WebP de até 900 KB. A imagem anterior foi preservada.", "error")
                photos = db().execute("SELECT id,kind,alt_text,position,updated_at FROM site_photos ORDER BY position").fetchall()
                return render_template("site_photos_settings.html", photos=photos), 400
            existing=db().execute("SELECT id FROM site_photos WHERE id=?",(photo_id,)).fetchone() if photo_id else None
            count=db().execute("SELECT COUNT(id) total FROM site_photos").fetchone()["total"]
            conflict=db().execute("SELECT id FROM site_photos WHERE position=? AND id!=?",(position,photo_id)).fetchone()
            if kind not in ("Fachada","Armazenamento","Atendimento") or position not in (1,2,3) or not alt or conflict or (not existing and count>=3) or (not existing and not data) or (data and not mime):
                flash("Confira tipo, ordem, descrição e imagem PNG, JPG ou WebP de até 900 KB.","error"); return redirect(url_for("site_photos_settings"))
            stamp=now().isoformat()
            if existing and data: db().execute("UPDATE site_photos SET kind=?,image=?,mime_type=?,alt_text=?,position=?,updated_at=? WHERE id=?",(kind,data,mime,alt,position,stamp,photo_id))
            elif existing: db().execute("UPDATE site_photos SET kind=?,alt_text=?,position=?,updated_at=? WHERE id=?",(kind,alt,position,stamp,photo_id))
            else: db().execute("INSERT INTO site_photos(kind,image,mime_type,alt_text,position,updated_at) VALUES(?,?,?,?,?,?)",(kind,data,mime,alt,position,stamp))
            audit("salvar_foto_local","foto",photo_id or None,kind); db().commit(); flash("Foto salva.","success"); return redirect(url_for("site_photos_settings"))
        photos=db().execute("SELECT id,kind,alt_text,position,updated_at FROM site_photos ORDER BY position").fetchall()
        return render_template("site_photos_settings.html",photos=photos)

    @app.route("/painel/configuracoes/aparencia", methods=["GET", "POST"])
    @admin_required
    def appearance_settings():
        defaults = dict(DEFAULT_THEME)
        if request.method == "POST":
            action=request.form.get("action")
            if action == "restore":
                db().execute("UPDATE settings SET active_theme_id=NULL WHERE id=1"); audit("restaurar_tema","configuracao",1); db().commit(); flash("Cores padrão restauradas.","success"); return redirect(url_for("appearance_settings"))
            if action == "activate":
                theme_id=int(request.form.get("theme_id","0")); found=db().execute("SELECT id FROM site_themes WHERE id=?",(theme_id,)).fetchone()
                if not found: abort(404)
                db().execute("UPDATE settings SET active_theme_id=? WHERE id=1",(theme_id,)); audit("ativar_tema","tema",theme_id); db().commit(); flash("Tema ativado.","success"); return redirect(url_for("appearance_settings"))
            if action == "delete":
                theme_id=int(request.form.get("theme_id","0")); found=db().execute("SELECT id FROM site_themes WHERE id=?",(theme_id,)).fetchone()
                if not found: abort(404)
                db().execute("UPDATE settings SET active_theme_id=NULL WHERE id=1 AND active_theme_id=?", (theme_id,))
                db().execute("DELETE FROM site_themes WHERE id=?", (theme_id,)); audit("excluir_tema","tema",theme_id); db().commit()
                flash("Tema excluído. As cores padrão foram restauradas se ele estava ativo.","success"); return redirect(url_for("appearance_settings"))
            name=request.form.get("name","").strip(); colors={key:request.form.get(key,"").strip().lower() for key in defaults}
            if not name or not all(re.fullmatch(r"#[0-9a-f]{6}",value) for value in colors.values()):
                flash("Informe um nome e cores válidas no formato hexadecimal.","error"); return render_template("appearance_settings.html",defaults=defaults,themes=[],form=colors),400
            def lum(color):
                values=[int(color[i:i+2],16)/255 for i in (1,3,5)]; values=[v/12.92 if v<=.03928 else ((v+.055)/1.055)**2.4 for v in values]; return .2126*values[0]+.7152*values[1]+.0722*values[2]
            ratio=(max(lum(colors["text"]),lum(colors["page"]))+.05)/(min(lum(colors["text"]),lum(colors["page"]))+.05)
            if ratio < 4.5:
                flash("A combinação entre texto e fundo prejudica o contraste; escolha cores mais distintas.","error"); return render_template("appearance_settings.html",defaults=defaults,themes=[],form=colors),400
            theme_id = int(request.form.get("theme_id", "0") or 0)
            if theme_id:
                updated=db().execute("UPDATE site_themes SET name=?,colors=? WHERE id=?",(name,json.dumps(colors),theme_id))
                if updated.rowcount != 1: abort(404)
                audit("editar_tema","tema",theme_id,name); db().commit(); flash("Alterações do tema salvas.","success")
            else:
                cur=db().execute("INSERT INTO site_themes(name,colors,created_at,created_by) VALUES(?,?,?,?) RETURNING id",(name,json.dumps(colors),now().isoformat(),session.get("staff")))
                theme_id=cur.fetchone()["id"]; db().execute("UPDATE settings SET active_theme_id=? WHERE id=1",(theme_id,)); audit("criar_tema","tema",theme_id,name); db().commit(); flash("Tema salvo e ativado.","success")
            return redirect(url_for("appearance_settings"))
        themes=db().execute("SELECT id,name,colors FROM site_themes ORDER BY id DESC LIMIT 50").fetchall()
        active=db().execute("SELECT active_theme_id FROM settings WHERE id=1").fetchone()["active_theme_id"]
        edit_id=request.args.get("edit", type=int); edit=next((theme for theme in themes if theme["id"]==edit_id),None)
        form=json.loads(edit["colors"]) if edit else defaults
        return render_template("appearance_settings.html",defaults=defaults,themes=themes,form=form,edit=edit,active_theme_id=active)

    @app.route("/painel/configuracoes/usuarios", methods=["GET", "POST"])
    @primary_required
    def staff_users():
        if request.method == "POST":
            name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower(); password=request.form.get("password",""); role=request.form.get("role")
            if len(name)<3 or not valid_email(email) or len(password)<8 or password != request.form.get("password_confirm") or role not in ("administrador","gerente","atendente"):
                flash("Confira nome, e-mail, perfil e a confirmação da senha de no mínimo 8 caracteres.","error")
            else:
                try:
                    primary = int(request.form.get("is_primary") == "yes")
                    permissions = tuple(int(role == "administrador" or request.form.get(key) == "yes") for key in ("can_clients", "can_packages", "can_pickups"))
                    prefix = {"administrador":"ADMPCA", "gerente":"GERPCA", "atendente":"ATEPCA"}[role]
                    public_id = next_public_code(db(), prefix)
                    cur=db().execute("INSERT INTO staff_users(public_id,name,email,password_hash,role,is_primary,active,can_settings,can_users,can_clients,can_packages,can_pickups,created_at,session_version) VALUES(?,?,?,?,?,?,1,?,?,?,?,?,?,1) RETURNING id",(public_id,name,email,generate_password_hash(password),role,primary,int(role=="administrador"),int(role=="administrador"),*permissions,now().isoformat()))
                    uid=cur.fetchone()["id"]; audit("criar_usuario_equipe","usuario_equipe",uid,f"perfil {role}"); db().commit(); flash("Usuário criado.","success")
                except DatabaseIntegrityError: db().rollback(); flash("Este e-mail já possui acesso.","error")
            return users_redirect("equipe")
        tab = request.args.get("tab", "clientes")
        if tab not in {"clientes", "equipe"}:
            tab = "clientes"
        client_query = request.args.get("q_clients", request.args.get("q", "")).strip()
        team_query = request.args.get("q_team", "").strip()
        query = client_query if tab == "clientes" else team_query
        like = f"%{query}%"
        page = max(1, request.args.get("page", 1, type=int))
        users, clients = [], []
        if tab == "clientes":
            where = "(?='' OR LOWER(name) LIKE LOWER(?) OR LOWER(public_id) LIKE LOWER(?) OR LOWER(legacy_public_id) LIKE LOWER(?) OR LOWER(email) LIKE LOWER(?) OR phone LIKE ?)"
            phone = digits(query) if re.fullmatch(r"[0-9\s()+.\-]+", query) else ""
            params = (query, like, like, like, like, f"%{phone}%" if phone else like)
            total = db().execute(f"SELECT COUNT(*) total FROM clients WHERE {where}", params).fetchone()["total"]
            pages = max(1, math.ceil(total / 50))
            page = min(page, pages)
            clients = db().execute(f"SELECT id,public_id,name,email,phone,active FROM clients WHERE {where} ORDER BY active DESC,name,id LIMIT 50 OFFSET ?", (*params, (page - 1) * 50)).fetchall()
        else:
            where = "deleted_at IS NULL AND (?='' OR LOWER(name) LIKE LOWER(?) OR LOWER(public_id) LIKE LOWER(?) OR LOWER(email) LIKE LOWER(?) OR LOWER(role) LIKE LOWER(?))"
            params = (query, like, like, like, like)
            total = db().execute(f"SELECT COUNT(*) total FROM staff_users WHERE {where}", params).fetchone()["total"]
            pages = max(1, math.ceil(total / 50))
            page = min(page, pages)
            users = db().execute(f"SELECT id,public_id,name,email,role,is_primary,active,can_settings,can_users,can_clients,can_packages,can_pickups,created_at FROM staff_users WHERE {where} ORDER BY active DESC,name,id LIMIT 50 OFFSET ?", (*params, (page - 1) * 50)).fetchall()
        legacy = db().execute("SELECT legacy_staff_active FROM settings WHERE id=1").fetchone()
        filters = {"tab": tab, "q_clients": client_query, "q_team": team_query, "page": page}
        return render_template("staff_users.html", users=users, clients=clients, tab=tab,
                               q=client_query, q_clients=client_query, q_team=team_query,
                               user_filters=filters, page=page, pages=pages, total=total,
                               legacy_active=legacy["legacy_staff_active"])

    def users_redirect(tab):
        return redirect(url_for("staff_users", tab=tab,
                                q_clients=request.values.get("q_clients", request.values.get("q", "")),
                                q_team=request.values.get("q_team", ""),
                                page=request.values.get("page", 1, type=int)))

    @app.post("/painel/configuracoes/clientes/<int:client_id>/alternar")
    @primary_required
    def admin_client_toggle(client_id):
        client=db().execute("SELECT id,active FROM clients WHERE id=?",(client_id,)).fetchone()
        if not client: abort(404)
        db().execute("UPDATE clients SET active=?,session_version=session_version+1 WHERE id=?",(0 if client["active"] else 1,client_id))
        audit("bloquear_cliente" if client["active"] else "ativar_cliente","cliente",client_id,"sessões encerradas")
        db().commit(); flash("Acesso do cliente atualizado e sessões encerradas.","success")
        return users_redirect("clientes")

    @app.post("/painel/configuracoes/clientes/<int:client_id>/encerrar-sessoes")
    @primary_required
    def admin_client_sessions(client_id):
        updated=db().execute("UPDATE clients SET session_version=session_version+1 WHERE id=?",(client_id,))
        if updated.rowcount != 1: abort(404)
        audit("encerrar_sessoes","cliente",client_id); db().commit(); flash("Sessões do cliente encerradas.","success")
        return users_redirect("clientes")

    @app.post("/painel/configuracoes/clientes/<int:client_id>/recuperar-senha")
    @primary_required
    def admin_client_recovery(client_id):
        client=db().execute("SELECT id,email,active FROM clients WHERE id=?",(client_id,)).fetchone()
        if not client: abort(404)
        if not client["active"] or not client["email"]:
            flash("Ative a conta e cadastre um e-mail antes de iniciar a recuperação.","error")
            return users_redirect("clientes")
        cutoff=(now()-timedelta(minutes=15)).isoformat()
        recent=db().execute("SELECT COUNT(id) total,MAX(created_at) latest FROM verification_codes WHERE client_id=? AND purpose='password_recovery' AND created_at>=?",(client_id,cutoff)).fetchone()
        if recent["total"]>=3 or (recent["latest"] and now()-datetime.fromisoformat(recent["latest"])<timedelta(seconds=60)):
            flash("Aguarde antes de solicitar um novo código.","error"); return users_redirect("clientes")
        code=f"{secrets.randbelow(1000000):06d}"; created=now().isoformat()
        sent,detail=send_code("email",client["email"],code)
        db().execute("INSERT INTO verification_codes(client_id,purpose,channel,destination,code_hash,expires_at,attempts,sends,provider_status,created_at,used_at) VALUES(?,?,?,?,?,?,0,1,?,?,?)",
            (client_id,"password_recovery","email",client["email"],hashlib.sha256(code.encode()).hexdigest(),(now()+timedelta(minutes=10)).isoformat(),detail,created,None if sent else created))
        audit("iniciar_recuperacao_senha","cliente",client_id,"envio por e-mail aceito" if sent else "falha do provedor")
        db().commit(); flash("O Gmail aceitou o envio do código." if sent else f"Não foi possível enviar o código. {detail}.","success" if sent else "error")
        return users_redirect("clientes")

    @app.post("/painel/configuracoes/usuarios/<int:user_id>/editar")
    @primary_required
    def staff_user_edit(user_id):
        user=db().execute("SELECT id,is_primary,role FROM staff_users WHERE id=? AND deleted_at IS NULL",(user_id,)).fetchone()
        if not user: abort(404)
        name=request.form.get("name","").strip(); email=request.form.get("email","").strip().lower(); role=request.form.get("role")
        active_admins=db().execute("SELECT COUNT(id) total FROM staff_users WHERE active=1 AND deleted_at IS NULL AND role='administrador'").fetchone()["total"]
        if len(name)<3 or not valid_email(email) or role not in ("administrador","gerente","atendente"): flash("Dados inválidos.","error")
        elif user["role"]=="administrador" and role!="administrador" and active_admins<=1: flash("O sistema deve manter ao menos um administrador ativo.","error")
        else:
            permissions = tuple(int(role == "administrador" or request.form.get(key) == "yes") for key in ("can_clients", "can_packages", "can_pickups"))
            try:
                db().execute("UPDATE staff_users SET name=?,email=?,role=?,can_settings=?,can_users=?,can_clients=?,can_packages=?,can_pickups=? WHERE id=?",(name,email,role,int(role=="administrador"),int(role=="administrador"),*permissions,user_id))
                audit("editar_usuario_equipe","usuario_equipe",user_id,f"perfil {role}"); db().commit(); flash("Usuário atualizado.","success")
            except DatabaseIntegrityError: db().rollback(); flash("Este e-mail já pertence a outro usuário.","error")
        return users_redirect("equipe")

    @app.post("/painel/configuracoes/usuarios/<int:user_id>/alternar")
    @primary_required
    def staff_user_toggle(user_id):
        user=db().execute("SELECT id,active,role FROM staff_users WHERE id=? AND deleted_at IS NULL",(user_id,)).fetchone()
        if not user: abort(404)
        if user["active"]:
            active_admins=db().execute("SELECT COUNT(id) total FROM staff_users WHERE active=1 AND deleted_at IS NULL AND role='administrador'").fetchone()["total"]
            if user["role"]=="administrador" and active_admins<=1: flash("Não é possível desativar o último administrador.","error"); return users_redirect("equipe")
        db().execute("UPDATE staff_users SET active=?,session_version=session_version+1 WHERE id=?",(0 if user["active"] else 1,user_id)); audit("alterar_acesso_equipe","usuario_equipe",user_id); db().commit(); flash("Acesso atualizado.","success"); return users_redirect("equipe")

    @app.post("/painel/configuracoes/usuarios/<int:user_id>/excluir")
    @primary_required
    def staff_user_delete(user_id):
        user=db().execute("SELECT id,role,active FROM staff_users WHERE id=? AND deleted_at IS NULL",(user_id,)).fetchone()
        if not user: abort(404)
        active_admins=db().execute("SELECT COUNT(id) total FROM staff_users WHERE active=1 AND deleted_at IS NULL AND role='administrador'").fetchone()["total"]
        if user["role"]=="administrador" and user["active"] and active_admins<=1:
            flash("Não é possível excluir o último administrador.","error")
        else:
            db().execute("UPDATE staff_users SET active=0,deleted_at=?,session_version=session_version+1 WHERE id=?",(now().isoformat(),user_id)); audit("excluir_usuario_equipe","usuario_equipe",user_id); db().commit(); flash("Usuário excluído e sessões encerradas. O histórico foi preservado.","success")
        return users_redirect("equipe")

    @app.post("/painel/configuracoes/usuarios/<int:user_id>/recuperar")
    @primary_required
    def staff_user_recovery(user_id):
        account=db().execute("SELECT id,email FROM staff_users WHERE id=? AND active=1 AND deleted_at IS NULL",(user_id,)).fetchone()
        if not account: abort(404)
        cutoff=(now()-timedelta(minutes=15)).isoformat(); recent=db().execute("SELECT COUNT(id) total,MAX(created_at) latest FROM staff_recovery_codes WHERE staff_user_id=? AND created_at>=?",(user_id,cutoff)).fetchone()
        if recent["total"]>=3 or (recent["latest"] and now()-datetime.fromisoformat(recent["latest"])<timedelta(seconds=60)):
            flash("Aguarde antes de solicitar um novo código.","error"); return users_redirect("equipe")
        code=f"{secrets.randbelow(1000000):06d}"; created=now().isoformat(); sent,detail=send_code("email",account["email"],code)
        db().execute("INSERT INTO staff_recovery_codes(staff_user_id,code_hash,expires_at,provider_status,created_at,used_at) VALUES(?,?,?,?,?,?)",(account["id"],hashlib.sha256(code.encode()).hexdigest(),(now()+timedelta(minutes=10)).isoformat(),detail,created,None if sent else created)); db().commit()
        flash("O Gmail aceitou o envio do código." if sent else f"Não foi possível enviar o código. {detail}.","success" if sent else "error")
        return users_redirect("equipe")

    @app.post("/painel/configuracoes/usuarios/<int:user_id>/encerrar-sessoes")
    @primary_required
    def staff_user_sessions(user_id):
        updated=db().execute("UPDATE staff_users SET session_version=session_version+1 WHERE id=? AND deleted_at IS NULL",(user_id,))
        if updated.rowcount != 1: abort(404)
        audit("encerrar_sessoes","usuario_equipe",user_id); db().commit()
        flash("Sessões do membro da equipe encerradas.","success")
        return users_redirect("equipe")

    @app.post("/painel/configuracoes/usuarios/desativar-legado")
    @primary_required
    def disable_legacy_staff():
        principals=db().execute("SELECT COUNT(id) total FROM staff_users WHERE active=1 AND deleted_at IS NULL AND role='administrador' AND last_login_at IS NOT NULL").fetchone()["total"]
        if not principals: flash("Crie a nova conta de administrador e confirme um login nela antes de desativar o acesso anterior.","error")
        else: db().execute("UPDATE settings SET legacy_staff_active=0 WHERE id=1"); audit("excluir_acesso_legado","configuracao",1); db().commit(); flash("Acesso administrativo anterior excluído e bloqueado.","success")
        return users_redirect("equipe")

    @app.get("/blog")
    def blog():
        posts = db().execute("SELECT slug,title,summary,cover_image,cover_alt,published_at,updated_at,layout FROM blog_posts WHERE status='published' ORDER BY published_at DESC LIMIT 30").fetchall()
        return render_template("blog.html", posts=posts)

    @app.get("/blog/<slug>")
    def blog_post(slug):
        if slug == "como-funciona-guarda-aqui-adelinha":
            return redirect(url_for("blog_post", slug="como-funciona-me-guarde-ponto-de-coleta-adelinha"), code=301)
        post = db().execute("SELECT slug,title,summary,body,cover_image,cover_alt,seo_title,seo_description,published_at,updated_at,layout,toc_enabled FROM blog_posts WHERE slug=? AND status='published'", (slug,)).fetchone()
        if not post: abort(404)
        return render_template("blog_post.html", post=post, **blog_tools.article_context(db(),post,format_blog_body))

    @app.get("/blog/<slug>/capa")
    def blog_cover(slug):
        post = db().execute("SELECT cover_image FROM blog_posts WHERE slug=? AND status='published'", (slug,)).fetchone()
        if not post or not post["cover_image"]: abort(404)
        data = bytes(post["cover_image"])
        mime = "image/png" if data.startswith(b"\x89PNG") else ("image/webp" if data.startswith(b"RIFF") else "image/jpeg")
        response=Response(data,mimetype=mime,headers={'Cache-Control':'public, max-age=86400','X-Content-Type-Options':'nosniff'})
        response.set_etag(hashlib.sha256(data).hexdigest())
        return response.make_conditional(request)

    def blog_editor(edit=None,status=200):
        posts=db().execute("SELECT id,slug,title,status,updated_at FROM blog_posts ORDER BY updated_at DESC LIMIT 50").fetchall()
        if edit is not None:
            edit=dict(edit)
            edit['body']=html.unescape(edit.get('body',''))
            original=db().execute('SELECT cover_image FROM blog_posts WHERE '+('id=?' if edit.get('id') else 'slug=?'),(edit.get('id') or edit.get('slug',''),)).fetchone()
            edit['cover_preview_src']=blog_tools.cover_data_uri(original['cover_image']) if original and original['cover_image'] else None
        return render_template('blog_admin.html',posts=posts,edit=edit,**blog_tools.editor_context(db(),current_staff)),status

    @app.route("/painel/blog", methods=["GET", "POST"])
    @admin_required
    def blog_admin():
        if request.method == "POST":
            title,slug=request.form.get('title','').strip(),request.form.get('slug','').strip().lower()
            slug=re.sub(r'[^a-z0-9-]','',slug)
            status='published' if request.form.get('action')=='publish' else 'draft'
            body=str(escape(request.form.get('body','')))
            layout=request.form.get('layout','legacy')
            values={**request.form,'id':request.form.get('post_id'),'body':body,'layout':layout,'toc_enabled':int(request.form.get('toc_enabled')=='yes')}
            if not title or not slug or not request.form.get('body','').strip() or layout not in ('legacy','icons'):
                flash('Informe título, endereço, conteúdo e um modelo de artigo válido.','error');return blog_editor(values,400)
            if request.form.get('post_id'):
                original=db().execute('SELECT slug FROM blog_posts WHERE id=?',(request.form['post_id'],)).fetchone()
                if not original or original['slug']!=slug:
                    flash('O endereço de um artigo existente deve ser preservado. Reabra o artigo para editar.','error');return blog_editor(values,400)
            try:cover,cover_alt=blog_tools.resolve_cover(db(),request.form,request.files,validated_image_type)
            except ValueError as error:
                values['remove_cover']=''
                flash(str(error),'error');return blog_editor(values,400)
            now_value=now().isoformat()
            db().execute("""INSERT INTO blog_posts(slug,title,summary,body,cover_image,cover_alt,seo_title,seo_description,status,published_at,updated_at,layout,toc_enabled)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(slug) DO UPDATE SET title=excluded.title,summary=excluded.summary,body=excluded.body,
                cover_image=excluded.cover_image,cover_alt=excluded.cover_alt,seo_title=excluded.seo_title,seo_description=excluded.seo_description,
                status=excluded.status,published_at=COALESCE(blog_posts.published_at,excluded.published_at),updated_at=excluded.updated_at,layout=excluded.layout,toc_enabled=excluded.toc_enabled""",
                (slug,title,request.form.get('summary','').strip(),body,cover,cover_alt,request.form.get('seo_title','').strip(),request.form.get('seo_description','').strip(),status,now_value if status=='published' else None,now_value,layout,int(request.form.get('toc_enabled')=='yes')))
            db().commit();flash('Artigo salvo com sucesso!','success');return redirect(url_for('blog_admin'))
        edit=db().execute('SELECT id,slug,title,summary,body,cover_alt,seo_title,seo_description,status,layout,toc_enabled FROM blog_posts WHERE id=?',(request.args['edit'],)).fetchone() if request.args.get('edit') else None
        return blog_editor(edit)

    @app.post("/painel/blog/<int:post_id>/despublicar")
    @admin_required
    def blog_unpublish(post_id):
        db().execute("UPDATE blog_posts SET status='draft',updated_at=? WHERE id=?", (now().isoformat(), post_id)); db().commit()
        flash("Artigo retirado da publicação.", "success"); return redirect(url_for("blog_admin"))

    @app.post("/painel/blog/<int:post_id>/excluir")
    @admin_required
    def blog_delete(post_id):
        deleted=db().execute("DELETE FROM blog_posts WHERE id=?",(post_id,))
        if deleted.rowcount != 1: abort(404)
        audit("excluir_artigo","artigo",post_id); db().commit()
        flash("Artigo excluído com sucesso!","success")
        return redirect(url_for("blog_admin"))

    @app.get("/robots.txt")
    def robots():
        return Response(f"User-agent: *\nDisallow: /painel\nDisallow: /cliente\nSitemap: {url_for('sitemap', _external=True)}\n", mimetype="text/plain")

    @app.get("/sitemap.xml")
    def sitemap():
        posts = db().execute("SELECT slug,updated_at FROM blog_posts WHERE status='published' ORDER BY id LIMIT 1000").fetchall()
        urls = [url_for("index", _external=True), url_for("faq", _external=True), url_for("blog", _external=True)] + [url_for("blog_post", slug=p["slug"], _external=True) for p in posts]
        return Response('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(f'<url><loc>{escape(u)}</loc></url>' for u in urls) + '</urlset>', mimetype="application/xml")

    @app.get("/health")
    def health():
        """Endpoint simples usado pela hospedagem para confirmar que o app iniciou."""
        db().execute("SELECT 1").fetchone()
        return {"status": "ok"}

    def presented_contract_matches(contract=None):
        current_version, _, current_text = contract or current_contract()
        version = request.form.get('terms_version')
        digest = request.form.get('terms_digest')
        # Compatibility for existing isolated fixtures, never for production.
        if app.config.get('TESTING') and version is None and digest is None:
            return True
        return version == current_version and digest == hashlib.sha256(current_text.encode()).hexdigest()

    @app.post("/cadastro")
    def register():
        version,label,text = current_contract()
        if not presented_contract_matches((version,label,text)):
            flash('O contrato foi atualizado. Leia a versão vigente e solicite um novo código antes de aceitar.', 'error')
            return render_template('index.html', registration_form={**request.form}), 409
        name = request.form.get("name", "").strip()
        cpf = digits(request.form.get("cpf", ""))
        phone = digits(request.form.get("phone", ""))
        email = request.form.get("email", "").strip().lower()
        signature = request.form.get("signature", "")
        password = request.form.get("password", "")
        residential = residential_address_form()
        confirmation = session.get("registration_confirmation")
        submitted_code = request.form.get("confirmation_code", "")
        confirmed = bool(confirmation and confirmation.get("cpf") == cpf and confirmation.get("email") == email and confirmation.get("version") == version
            and confirmation.get("text_digest") == hashlib.sha256(text.encode()).hexdigest()
            and confirmation.get("attempts", 0) < 5
            and datetime.fromisoformat(confirmation["expires_at"]) > now()
            and secrets.compare_digest(confirmation["code_hash"], hashlib.sha256(submitted_code.encode()).hexdigest()))
        if not app.config.get("TESTING") and not confirmed:
            if confirmation:
                confirmation["attempts"] = confirmation.get("attempts", 0) + 1
                session["registration_confirmation"] = confirmation
            flash("Confirme o código enviado ao e-mail antes de concluir.", "error")
            return render_template("index.html", registration_form={**request.form}), 400
        if request.form.get("terms_accept") != "yes":
            flash("É necessário aceitar as condições do serviço.", "error")
            return redirect(url_for("index") + "#cadastro")
        form = {"name": name, "cpf": request.form.get("cpf", ""), "phone": request.form.get("phone", ""), "email": email, **residential}
        if not valid_cpf(cpf):
            flash("CPF inválido. Confira os números digitados.", "error")
            return render_template("index.html", registration_form=form), 400
        if len(name) < 5 or len(phone) < 10 or not email or not valid_email(email) or not signature.startswith("data:image/png;base64,") or len(password) < 8 or password != request.form.get("password_confirm") or not valid_residential_address(residential):
            flash("Confira os dados, o e-mail, o endereço residencial obrigatório, a assinatura e as senhas (mínimo de 8 caracteres).", "error")
            return render_template("index.html", registration_form=form), 400
        accepted_at = now().isoformat()
        if db().execute("SELECT 1 FROM clients WHERE REPLACE(REPLACE(REPLACE(cpf,'.',''),'-',''),' ','')=?", (cpf,)).fetchone():
            flash("Este CPF já possui cadastro.", "error")
            return render_template("index.html", registration_form=form), 409
        public_id = next_public_code(db(), "CLIPCA")
        cur = db().execute(
            """INSERT INTO clients(public_id,name, cpf, phone, terms_version, terms_text, signature, accepted_at, password_hash,
               residential_cep,residential_street,residential_number,residential_district,residential_city,residential_state,residential_complement,email)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
            (public_id,name, cpf, phone, version, text, signature, accepted_at, generate_password_hash(password),
             digits(residential["residential_cep"]), residential["residential_street"], residential["residential_number"],
             residential["residential_district"], residential["residential_city"], residential["residential_state"].upper(),
             residential["residential_complement"] or None, email or None),
        )
        client_id = cur.fetchone()["id"]
        integrity = hashlib.sha256((public_id + name + cpf + text + signature + accepted_at).encode()).hexdigest()
        db().execute("UPDATE clients SET contract_id=?,contract_hash=?,contract_public_id=?,document_brand_version=?,document_logo_present=?,confirmation_channel=?,confirmation_destination=?,confirmation_validated_at=?,accept_ip=?,accept_user_agent=?,terms_display_label=?,contract_revision_id=? WHERE id=?",
            (f"CTRPCA-{client_id}", integrity, public_id, BRAND_VERSION, int(ORIGINAL_LOGO.is_file()), confirmation.get("channel") if confirmation else "teste",
             confirmation.get("destination") if confirmation else None, accepted_at,
             request.headers.get("X-Forwarded-For", request.remote_addr), request.headers.get("User-Agent", "")[:500], label, revision_id(version,text), client_id))
        db().commit()
        session.pop("registration_confirmation", None)
        session.pop("google_registration_required", None)
        return render_template("success.html", name=name, public_id=public_id, address=ADDRESS, contract_label=label, contract_version=version)

    @app.post("/cadastro/enviar-codigo")
    def registration_code():
        version,label,text = current_contract()
        if not presented_contract_matches((version,label,text)):
            return {'error': 'O contrato foi atualizado. Recarregue, leia a versão vigente e tente novamente.'}, 409
        cpf, channel = digits(request.form.get("cpf", "")), "email"
        if request.form.get("confirmation_channel") not in (None,"","email"):
            return {"error":"A confirmação do contrato está disponível somente por e-mail."},400
        destination = request.form.get("email", "").strip().lower()
        previous = session.get("registration_confirmation", {})
        if previous.get("cpf") != cpf or previous.get("email") != destination:
            previous = {}
        if previous.get("sends", 0) >= 3:
            return {"error": "Limite de reenvios atingido. Aguarde e tente novamente."}, 429
        if previous.get("sent_at") and now()-datetime.fromisoformat(previous["sent_at"]) < timedelta(seconds=60):
            return {"error":"Aguarde 60 segundos antes de reenviar o código."},429
        if not valid_cpf(cpf) or not valid_email(destination) or not destination:
            return {"error": "Confira o CPF e o e-mail informado."}, 400
        code = f"{secrets.randbelow(1000000):06d}"
        try: sent, detail = send_code(channel, destination, code)
        except Exception: sent, detail = False, "Falha no provedor"
        if not sent: return {"error": f"Envio indisponível: {detail}."}, 503
        session["registration_confirmation"] = {"cpf":cpf,"email":destination,"version":version,"text_digest":hashlib.sha256(text.encode()).hexdigest(),"channel":channel,
            "destination":destination,"code_hash":hashlib.sha256(code.encode()).hexdigest(),
            "expires_at":(now()+timedelta(minutes=10)).isoformat(),"sent_at":now().isoformat(),"attempts":0,"sends":previous.get("sends",0)+1}
        return {"message":"Código enviado. Ele expira em 10 minutos."}

    @app.get('/privacidade')
    def privacy():
        back=request.args.get('next','/')
        if not back.startswith('/') or back.startswith('//') or '\\' in back or any(ord(c)<32 for c in back):
            back=url_for('index')
        return render_template('privacy.html',back=back,updated=PRIVACY_UPDATED,contact=PRIVACY_EMAIL,review_pending=PRIVACY_REVIEW_PENDING)

    @app.get("/condicoes")
    def terms():
        back = request.args.get("next", "")
        if not back.startswith("/") or back.startswith("//"):
            back = url_for("index")
        version,label,text=current_contract()
        return render_template("terms.html", version=version, exact_text=text, back=back, contract_label=label,document_revision_id=revision_id(version,text))

    @app.get('/condicoes/pdf')
    def current_contract_pdf():
        version,label,text=current_contract()
        return send_file(build_current_contract_pdf(text, version, label), mimetype='application/pdf',
                         as_attachment=True, download_name=f'contrato-{version}.pdf')

    @app.get("/login")
    def login():
        return render_template("login.html")

    @app.post("/login")
    def login_post():
        user = request.form.get("user", "").strip().lower()
        if login_blocked("staff", user):
            flash("Muitas tentativas. Aguarde 15 minutos.", "error")
            return redirect(url_for("login"))
        account = db().execute("SELECT id,name,email,password_hash,role,is_primary,active,session_version,deleted_at FROM staff_users WHERE LOWER(email)=?", (user,)).fetchone()
        valid_account = bool(account and account["active"] and not account["deleted_at"] and check_password_hash(account["password_hash"], request.form.get("password", "")))
        legacy_setting = db().execute("SELECT legacy_staff_active FROM settings WHERE id=1").fetchone()
        valid_legacy = bool((not legacy_setting or legacy_setting["legacy_staff_active"]) and user == app.config["STAFF_USER"].lower() and check_password_hash(app.config["STAFF_PASSWORD_HASH"], request.form.get("password", "")))
        record_login("staff", user, valid_account or valid_legacy)
        if valid_account or valid_legacy:
            session.clear(); session["staff"] = account["email"] if valid_account else app.config["STAFF_USER"]
            if valid_account:
                session["staff_user_id"] = account["id"]; session["staff_session_version"] = account["session_version"]
                db().execute("UPDATE staff_users SET last_login_at=? WHERE id=?",(now().isoformat(),account["id"])); db().commit()
            else: session["staff_legacy"] = True
            return redirect(url_for("dashboard"))
        flash("Usuário ou senha inválidos.", "error")
        return redirect(url_for("login"))

    @app.route("/equipe/recuperar-senha", methods=["GET", "POST"])
    def staff_password_recovery():
        if request.method == "GET": return render_template("staff_password_recovery.html", stage="request")
        if request.form.get("channel") not in (None, "", "email"):
            flash("A recuperação de senha da equipe está disponível somente por e-mail.", "error")
            return render_template("staff_password_recovery.html", stage="request"), 400
        stage=request.form.get("stage")
        if stage == "request":
            email=request.form.get("email","").strip().lower(); account=db().execute("SELECT id,email FROM staff_users WHERE LOWER(email)=? AND active=1 AND deleted_at IS NULL",(email,)).fetchone()
            if not account:
                flash("Se o e-mail estiver cadastrado, enviaremos as instruções.","success"); return render_template("staff_password_recovery.html",stage="confirm")
            cutoff=(now()-timedelta(minutes=15)).isoformat(); recent=db().execute("SELECT COUNT(id) total,MAX(created_at) latest FROM staff_recovery_codes WHERE staff_user_id=? AND created_at>=?",(account["id"],cutoff)).fetchone()
            if recent["total"]>=3 or (recent["latest"] and now()-datetime.fromisoformat(recent["latest"])<timedelta(seconds=60)):
                flash("Aguarde antes de solicitar um novo código.","error"); return render_template("staff_password_recovery.html",stage="request"),429
            code=f"{secrets.randbelow(1000000):06d}"; created=now().isoformat(); sent,detail=send_code("email",account["email"],code)
            cur=db().execute("INSERT INTO staff_recovery_codes(staff_user_id,code_hash,expires_at,provider_status,created_at,used_at) VALUES(?,?,?,?,?,?) RETURNING id",(account["id"],hashlib.sha256(code.encode()).hexdigest(),(now()+timedelta(minutes=10)).isoformat(),detail,created,None if sent else created)); code_id=cur.fetchone()["id"]; db().commit()
            if not sent: flash(f"Não foi possível enviar o código. {detail}.","error"); return render_template("staff_password_recovery.html",stage="request"),503
            session["staff_recovery_id"]=code_id; session["staff_recovery_email"]=email; flash("O Gmail aceitou o envio do código.","success"); return render_template("staff_password_recovery.html",stage="confirm",masked_contact=mask_contact("email",email),recovery_email=email)
        recovery_id=session.get("staff_recovery_id")
        if recovery_id:
            row=db().execute("SELECT id,staff_user_id,code_hash,expires_at,attempts,used_at FROM staff_recovery_codes WHERE id=?",(recovery_id,)).fetchone()
        else:
            recovery_email=request.form.get("email","").strip().lower()
            row=db().execute("SELECT rc.id,rc.staff_user_id,rc.code_hash,rc.expires_at,rc.attempts,rc.used_at FROM staff_recovery_codes rc JOIN staff_users su ON su.id=rc.staff_user_id WHERE LOWER(su.email)=? AND su.active=1 AND su.deleted_at IS NULL ORDER BY rc.id DESC LIMIT 1",(recovery_email,)).fetchone()
        password=request.form.get("password",""); valid=bool(row and not row["used_at"] and row["attempts"]<5 and datetime.fromisoformat(row["expires_at"])>now() and secrets.compare_digest(row["code_hash"],hashlib.sha256(request.form.get("code","").encode()).hexdigest()))
        if not valid or len(password)<8 or password!=request.form.get("password_confirm"):
            if row: db().execute("UPDATE staff_recovery_codes SET attempts=attempts+1 WHERE id=?",(row["id"],)); db().commit()
            flash("Código inválido ou expirado, ou as senhas não conferem.","error"); return render_template("staff_password_recovery.html",stage="confirm",recovery_email=session.get("staff_recovery_email",request.form.get("email",""))),400
        db().execute("UPDATE staff_users SET password_hash=?,session_version=session_version+1 WHERE id=?",(generate_password_hash(password),row["staff_user_id"])); db().execute("UPDATE staff_recovery_codes SET used_at=? WHERE id=?",(now().isoformat(),row["id"])); db().commit(); session.pop("staff_recovery_id",None); session.pop("staff_recovery_email",None); flash("Senha alterada com sucesso!","success"); return redirect(url_for("login"))


    @app.get("/cliente/login")
    def client_login():
        return render_template("client_login.html")

    @app.route("/cliente/recuperar-senha", methods=["GET", "POST"])
    def password_recovery():
        if request.method == "GET":
            return render_template("password_recovery.html", stage="request")
        stage = request.form.get("stage")
        if stage == "request":
            session.pop("password_recovery_id", None)
            cpf, channel = digits(request.form.get("cpf", "")), "email"
            if request.form.get("channel") not in (None, "", "email"):
                flash("A recuperação de senha está disponível somente por e-mail.", "error")
                return render_template("password_recovery.html", stage="request", entered_cpf=request.form.get("cpf", "")), 400
            client = db().execute("SELECT id,phone,email FROM clients WHERE REPLACE(REPLACE(REPLACE(cpf,'.',''),'-',''),' ','')=? AND active=1", (cpf,)).fetchone()
            # Para uma conta inexistente, mantém uma resposta neutra e não revela cadastros.
            if not client:
                flash("Se os dados estiverem corretos, enviaremos as instruções ao contato cadastrado.", "success")
                return render_template("password_recovery.html", stage="confirm")
            destination = client["email"]
            if not destination:
                flash("Este cadastro não possui e-mail disponível. Procure a equipe para atualizar o endereço de e-mail.", "error")
                return render_template("password_recovery.html", stage="request", entered_cpf=request.form.get("cpf", "")), 400
            cutoff = (now() - timedelta(minutes=15)).isoformat()
            recent = db().execute("SELECT COUNT(id) AS total,MAX(created_at) latest FROM verification_codes WHERE client_id=? AND purpose='password_recovery' AND created_at>=?", (client["id"], cutoff)).fetchone()
            if recent["total"] >= 3 or (recent["latest"] and now()-datetime.fromisoformat(recent["latest"])<timedelta(seconds=60)):
                flash("Aguarde antes de solicitar um novo código.", "error")
                return render_template("password_recovery.html", stage="request", entered_cpf=request.form.get("cpf", "")), 429
            code = f"{secrets.randbelow(1000000):06d}"
            created_at, expires = now().isoformat(), (now() + timedelta(minutes=10)).isoformat()
            sent, detail = send_code(channel, destination, code)
            cur = db().execute("INSERT INTO verification_codes(client_id,purpose,channel,destination,code_hash,expires_at,attempts,sends,provider_status,created_at,used_at) VALUES(?,?,?,?,?,?,0,1,?,?,?) RETURNING id",
                (client["id"], "password_recovery", channel, destination, hashlib.sha256(code.encode()).hexdigest(), expires, detail, created_at, None if sent else created_at))
            verification_id = cur.fetchone()["id"]
            if sent:
                db().execute("UPDATE verification_codes SET used_at=? WHERE client_id=? AND purpose='password_recovery' AND id<>? AND used_at IS NULL", (created_at, client["id"], verification_id))
                session["password_recovery_id"] = verification_id
                session["password_recovery_contact"] = mask_contact(channel, destination)
            db().commit()
            if not sent:
                flash(f"Não foi possível enviar o código por e-mail. {detail}. Confira os dados para tentar novamente.", "error")
                return render_template("password_recovery.html", stage="request", entered_cpf=request.form.get("cpf", "")), 503
            flash("O provedor aceitou o envio. Confira o contato cadastrado; o código expira em 10 minutos.", "success")
            return render_template("password_recovery.html", stage="confirm", masked_contact=session.get("password_recovery_contact"))
        verification_id = session.get("password_recovery_id")
        row = db().execute("SELECT id,client_id,code_hash,expires_at,attempts,used_at FROM verification_codes WHERE id=? AND purpose='password_recovery'", (verification_id,)).fetchone() if verification_id else None
        password = request.form.get("password", "")
        valid = bool(row and not row["used_at"] and row["attempts"] < 5 and datetime.fromisoformat(row["expires_at"]) > now() and secrets.compare_digest(row["code_hash"], hashlib.sha256(request.form.get("code", "").encode()).hexdigest()))
        if not valid or len(password) < 8 or password != request.form.get("password_confirm"):
            if row: db().execute("UPDATE verification_codes SET attempts=attempts+1 WHERE id=?", (row["id"],)); db().commit()
            flash("Código inválido ou expirado, ou as senhas não conferem.", "error")
            return render_template("password_recovery.html", stage="confirm", masked_contact=session.get("password_recovery_contact")), 400
        db().execute("UPDATE clients SET password_hash=?,session_version=session_version+1 WHERE id=?", (generate_password_hash(password), row["client_id"]))
        db().execute("UPDATE verification_codes SET used_at=? WHERE id=?", (now().isoformat(), row["id"])); db().commit()
        session.pop("password_recovery_id", None); session.pop("password_recovery_contact", None)
        flash("Senha alterada com sucesso!", "success")
        return redirect(url_for("client_login"))

    @app.post("/cliente/login")
    def client_login_post():
        identifier = request.form.get("cpf", "").strip().lower()[:254]
        cpf = identifier if "@" in identifier else digits(identifier)
        if login_blocked("client", cpf):
            flash("Muitas tentativas. Aguarde 15 minutos.", "error")
            return redirect(url_for("client_login"))
        if '@' in cpf:
            matches = db().execute("SELECT id,password_hash,active,session_version FROM clients WHERE LOWER(TRIM(email))=? LIMIT 2", (cpf,)).fetchall()
            client = matches[0] if len(matches) == 1 else None
        else:
            client = db().execute("""SELECT id,password_hash,active,session_version FROM clients
                WHERE REPLACE(REPLACE(REPLACE(cpf,'.',''),'-',''),' ','')=?""", (cpf,)).fetchone()
        valid = bool(client and client["active"] and client["password_hash"] and check_password_hash(client["password_hash"], request.form.get("password", "")))
        record_login("client", cpf, valid)
        if valid:
            destination = session.get("client_login_destination")
            session.clear()
            session["client_id"] = client["id"]
            session["client_session_version"] = client["session_version"]
            return redirect(url_for("plans.client_hub" if destination == "plan" else "client_area"))
        flash("CPF, e-mail ou senha inválidos. Se você ainda não possui senha, procure a equipe.", "error")
        return redirect(url_for("client_login"))

    @app.post("/cliente/sair")
    def client_logout():
        session.clear()
        return redirect(url_for("index"))

    @app.get("/cliente")
    @client_login_required
    def client_area():
        client = db().execute("SELECT * FROM clients WHERE id=?", (session["client_id"],)).fetchone()
        accepted_record, pending_items = compliance(db(), client)
        page, page_size, offset = pagination()
        package_rows = db().execute(f"SELECT {PACKAGE_COLUMNS} FROM packages WHERE client_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?", (client["id"], page_size + 1, offset)).fetchall()
        has_next = len(package_rows) > page_size
        packages = package_rows[:page_size]
        pending = []
        for package in packages:
            if package["status"] != "retirado":
                item = {**dict(package), **fees(package)}
                item["deadline"] = (datetime.fromisoformat(package["notified_at"]).astimezone(TZ).date() + timedelta(days=3)).strftime("%d/%m/%Y") if package["notified_at"] else None
                if package["notified_at"]:
                    day = (now().date() - datetime.fromisoformat(package["notified_at"]).astimezone(TZ).date()).days + 1
                    item["deadline_alert"] = "ending" if day in (3, 4) else ("late" if day >= 5 else None)
                pending.append(item)
        return render_template("client_area.html", client=client, pending=pending, packages=packages, page=page, has_next=has_next, pending_items=pending_items, accepted_record=accepted_record, accepted_signature=signature_data(accepted_record.get("signature")))

    @app.route("/cliente/editar", methods=["GET", "POST"])
    @client_login_required
    def client_edit():
        client = db().execute(f"SELECT {CLIENT_COLUMNS} FROM clients WHERE id=?", (session["client_id"],)).fetchone()
        if request.method == "GET":
            return render_template("client_edit.html", client=client)
        phone = digits(request.form.get("phone", ""))
        email = request.form.get("email", "").strip().lower()
        residential = residential_address_form()
        if len(phone) < 10 or not valid_email(email) or not valid_residential_address(residential):
            flash("Confira o WhatsApp, o e-mail e todos os campos obrigatórios do endereço residencial.", "error")
            values = {**dict(client), **residential, "phone": request.form.get("phone", ""), "email": email}
            return render_template("client_edit.html", client=values), 400
        db().execute("""UPDATE clients SET phone=?,email=?,residential_cep=?,residential_street=?,residential_number=?,
            residential_district=?,residential_city=?,residential_state=?,residential_complement=? WHERE id=?""",
            (phone, email or None, digits(residential["residential_cep"]), residential["residential_street"],
             residential["residential_number"], residential["residential_district"], residential["residential_city"],
             residential["residential_state"].upper(), residential["residential_complement"] or None, session["client_id"]))
        audit("editar_proprio_cadastro", "cliente", session["client_id"], "telefone, e-mail ou endereço residencial atualizados")
        db().commit()
        flash("Dados atualizados com sucesso", "success")
        return redirect(url_for("client_area"))

    @app.get("/cliente/contrato-assinado.pdf")
    @client_login_required
    def client_contract_pdf():
        client = db().execute(f"SELECT {CLIENT_COLUMNS} FROM clients WHERE id=?", (session["client_id"],)).fetchone()
        client = effective_record(db(), db().execute('SELECT * FROM clients WHERE id=?',(session['client_id'],)).fetchone())
        return send_file(build_contract_pdf(client), mimetype="application/pdf", as_attachment=True, download_name=f"contrato-{client['public_id']}.pdf")

    @app.get("/cliente/condicoes-aceitas")
    @client_login_required
    def client_accepted_terms():
        client = db().execute(f"SELECT {CLIENT_COLUMNS} FROM clients WHERE id=?", (session["client_id"],)).fetchone()
        client = effective_record(db(), db().execute('SELECT * FROM clients WHERE id=?',(session['client_id'],)).fetchone())
        return render_template("accepted_terms.html", client=client, signature_preview=signature_data(client.get('signature')))

    @app.get("/cliente/comprovantes/<int:pickup_id>")
    @client_login_required
    def client_receipt(pickup_id):
        pickup = db().execute(f"SELECT {PICKUP_COLUMNS_PU} FROM pickups pu WHERE pu.id=? AND EXISTS(SELECT 1 FROM pickup_packages pp JOIN packages p ON p.id=pp.package_id WHERE pp.pickup_id=pu.id AND p.client_id=?)", (pickup_id, session["client_id"])).fetchone()
        if not pickup:
            abort(404)
        packages = db().execute("SELECT p.id package_id,p.public_id,pp.base_amount,pp.late_amount FROM pickup_packages pp JOIN packages p ON p.id=pp.package_id WHERE pp.pickup_id=? AND p.client_id=?", (pickup_id, session["client_id"])).fetchall()
        return render_template("receipt.html", pickup=pickup, packages=packages, pix=pickup["pix_key_snapshot"] or PIX_KEY, client_copy=True)

    @app.post("/sair")
    def logout():
        session.clear()
        return redirect(url_for("index"))

    @app.get("/painel")
    @login_required
    def dashboard():
        query = request.args.get("q", "").strip()
        clients = find_clients(query)
        page, page_size, offset = pagination()
        package_rows = db().execute(f"""
            SELECT {PACKAGE_COLUMNS_P}, c.name client_name, c.public_id client_public_id, c.phone
            FROM packages p JOIN clients c ON c.id=p.client_id
            ORDER BY CASE p.status WHEN 'aguardando_aviso' THEN 0 WHEN 'aguardando_retirada' THEN 1 ELSE 2 END, p.created_at DESC
            LIMIT ? OFFSET ?
        """, (page_size + 1, offset)).fetchall()
        has_next = len(package_rows) > page_size
        package_rows = package_rows[:page_size]
        # Uma única coleção alimenta a única seção "Pacotes" da tela. A chave
        # também protege a interface caso uma futura consulta ganhe outro JOIN.
        packages = {package["id"]: package for package in package_rows}.values()
        rows = [{**dict(package), **fees(package)} for package in packages]
        alerts = {"today": [], "late": [], "expired": []}
        alert_rows = db().execute(f"""SELECT {PACKAGE_COLUMNS_P},c.name client_name,c.phone FROM packages p JOIN clients c ON c.id=p.client_id
            WHERE p.status='aguardando_retirada' AND p.notified_at IS NOT NULL ORDER BY p.notified_at LIMIT 200""").fetchall()
        for package in alert_rows:
            item = {**dict(package), **fees(package)}
            day = (now().date() - datetime.fromisoformat(package["notified_at"]).astimezone(TZ).date()).days + 1
            item["deadline"] = (datetime.fromisoformat(package["notified_at"]).astimezone(TZ).date() + timedelta(days=3)).strftime("%d/%m/%Y")
            if day == 4: alerts["today"].append(item)
            elif 5 <= day <= 10: alerts["late"].append(item)
            elif day >= 11: alerts["expired"].append(item)
        package_form = session.pop("package_form", {})
        settings = db().execute("SELECT pix_receiver_name,pix_key,pix_key_type FROM settings WHERE id=1").fetchone()
        return render_template("dashboard.html", clients=clients, packages=rows, alerts=alerts, q=query, pix=settings["pix_key"], pix_key_type=settings["pix_key_type"], package_form=package_form, pix_receiver_name=settings["pix_receiver_name"], page=page, has_next=has_next)

    @app.get("/painel/clientes/busca")
    @login_required
    def client_search():
        clients = find_clients(request.args.get("q", ""))
        return {"clients": [dict(client) for client in clients]}

    @app.get("/painel/clientes/<int:client_id>")
    @login_required
    def staff_client(client_id):
        client = db().execute(f"SELECT {CLIENT_COLUMNS} FROM clients WHERE id=?", (client_id,)).fetchone()
        if not client:
            abort(404)
        page, page_size, offset = pagination()
        package_rows = db().execute(f"SELECT {PACKAGE_COLUMNS} FROM packages WHERE client_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?", (client_id, page_size + 1, offset)).fetchall()
        has_next = len(package_rows) > page_size
        packages = package_rows[:page_size]
        pending_count = db().execute("SELECT COUNT(*) AS total FROM packages WHERE client_id=? AND status!='retirado'", (client_id,)).fetchone()["total"]
        linked_count = db().execute("SELECT COUNT(*) AS total FROM packages WHERE client_id=?", (client_id,)).fetchone()["total"]
        edit_form = session.pop(f"client_edit_form_{client_id}", None)
        return render_template("staff_client.html", client=client, edit_form=edit_form, packages=packages, pending_count=pending_count, linked_count=linked_count, page=page, has_next=has_next)

    @app.post("/painel/clientes/<int:client_id>/editar")
    @login_required
    def edit_client(client_id):
        client = db().execute(f"SELECT {CLIENT_COLUMNS} FROM clients WHERE id=?", (client_id,)).fetchone()
        if not client:
            abort(404)
        name = request.form.get("name", "").strip()
        cpf, phone = digits(request.form.get("cpf", "")), digits(request.form.get("phone", ""))
        email = request.form.get("email", "").strip().lower()
        residential = residential_address_form()
        edit_form = {"name": name, "cpf": request.form.get("cpf", ""), "phone": request.form.get("phone", ""), "email": email, **residential}
        if not valid_cpf(cpf):
            session[f"client_edit_form_{client_id}"] = edit_form
            flash("CPF inválido. Confira os números digitados.", "error")
            return redirect(url_for("staff_client", client_id=client_id))
        duplicate = db().execute("""SELECT 1 FROM clients
            WHERE REPLACE(REPLACE(REPLACE(cpf,'.',''),'-',''),' ','')=? AND id!=?""", (cpf, client_id)).fetchone()
        if duplicate:
            session[f"client_edit_form_{client_id}"] = edit_form
            flash("Este CPF já possui cadastro.", "error")
            return redirect(url_for("staff_client", client_id=client_id))
        if len(name) < 5 or len(phone) < 10 or not valid_email(email) or not valid_residential_address(residential):
            session[f"client_edit_form_{client_id}"] = edit_form
            flash("Confira nome, WhatsApp, e-mail e todos os campos obrigatórios do endereço residencial.", "error")
            return redirect(url_for("staff_client", client_id=client_id))
        db().execute("""UPDATE clients SET name=?,cpf=?,phone=?,email=?,residential_cep=?,residential_street=?,residential_number=?,
            residential_district=?,residential_city=?,residential_state=?,residential_complement=? WHERE id=?""",
            (name, cpf, phone, email or None, digits(residential["residential_cep"]), residential["residential_street"],
             residential["residential_number"], residential["residential_district"], residential["residential_city"],
             residential["residential_state"].upper(), residential["residential_complement"] or None, client_id))
        audit("editar", "cliente", client_id, "nome, CPF ou telefone atualizados")
        db().commit()
        flash("Cadastro atualizado. Contratos e comprovantes históricos não foram alterados.", "success")
        return redirect(url_for("staff_client", client_id=client_id))

    @app.post("/painel/clientes/<int:client_id>/senha")
    @login_required
    def admin_client_password(client_id):
        password = request.form.get("password", "")
        if len(password) < 8 or password != request.form.get("password_confirm"):
            flash("As senhas devem ser iguais e ter ao menos 8 caracteres.", "error")
            return redirect(url_for("staff_client", client_id=client_id))
        if not db().execute("SELECT 1 FROM clients WHERE id=?", (client_id,)).fetchone():
            abort(404)
        db().execute("UPDATE clients SET password_hash=?,session_version=session_version+1 WHERE id=?", (generate_password_hash(password), client_id))
        db().execute("UPDATE password_tokens SET used_at=? WHERE client_id=? AND used_at IS NULL", (now().isoformat(), client_id))
        audit("alterar_senha", "cliente", client_id, "sessões anteriores invalidadas")
        db().commit()
        flash("Nova senha definida e sessões anteriores invalidadas.", "success")
        return redirect(url_for("staff_client", client_id=client_id))

    @app.post("/painel/clientes/<int:client_id>/remover")
    @login_required
    def remove_client(client_id):
        client = db().execute(f"SELECT {CLIENT_COLUMNS} FROM clients WHERE id=?", (client_id,)).fetchone()
        if not client or request.form.get("confirmation") != client["public_id"]:
            flash("Confirmação inválida. Digite o ID exibido.", "error")
            return redirect(url_for("staff_client", client_id=client_id))
        pending = db().execute("SELECT COUNT(*) AS total FROM packages WHERE client_id=? AND status!='retirado'", (client_id,)).fetchone()["total"]
        linked = db().execute("SELECT COUNT(*) AS total FROM packages WHERE client_id=?", (client_id,)).fetchone()["total"]
        if pending:
            flash("Não é possível excluir ou desativar enquanto houver pacotes pendentes.", "error")
        elif db().execute("SELECT 1 FROM plan_accounts WHERE client_id=?", (client_id,)).fetchone() or linked or client["signature"] or client["terms_version"] or db().execute("SELECT 1 FROM client_contract_records WHERE client_id=?", (client_id,)).fetchone() or db().execute("SELECT 1 FROM client_action_assignments WHERE client_id=?", (client_id,)).fetchone():
            db().execute("UPDATE clients SET active=0,session_version=session_version+1 WHERE id=?", (client_id,))
            audit("desativar", "cliente", client_id, "histórico preservado")
            db().commit()
            flash("Cliente desativado. O histórico foi preservado e o acesso foi bloqueado.", "success")
        else:
            audit("excluir", "cliente", client_id, "sem registros vinculados")
            db().execute("DELETE FROM password_tokens WHERE client_id=?", (client_id,))
            db().execute("DELETE FROM clients WHERE id=?", (client_id,))
            db().commit()
            flash("Cliente excluído definitivamente.", "success")
            return redirect(url_for("dashboard"))
        return redirect(url_for("staff_client", client_id=client_id))

    @app.get("/painel/configuracoes/notificacoes")
    @admin_required
    def notification_settings():
        records = db().execute("SELECT channel,destination,provider_status,created_at FROM verification_codes ORDER BY id DESC LIMIT 50").fetchall()
        safe_records = [{"channel": row["channel"], "destination": mask_contact(row["channel"], row["destination"]),
                         "provider_status": row["provider_status"] or "Não registrado", "created_at": row["created_at"]}
                        for row in records]
        return render_template("notification_settings.html", status=notification_config_status(), records=safe_records)

    @app.route("/painel/configuracoes/cartoes-explicativos", methods=["GET", "POST"])
    @admin_required
    def explanatory_card_settings():
        staff = current_staff()
        if staff and staff["role"] != "administrador":
            abort(403)
        if request.method == "POST":
            try:
                card_id = int(request.form.get("card_id", "0"))
            except ValueError:
                abort(400)
            card = db().execute("SELECT id FROM explanatory_cards WHERE id=?", (card_id,)).fetchone()
            if not card:
                abort(404)
            title = request.form.get("title", "").strip()
            alt = request.form.get("alt_text", "").strip()
            upload = request.files.get("image")
            has_upload = upload is not None and bool(upload.filename)
            data = upload.read(IMAGE_UPLOAD_MAX_BYTES + 1) if has_upload else None
            mime = validated_image_type(data, IMAGE_UPLOAD_MAX_BYTES) if has_upload else None
            error = None
            derivatives = None
            if not title or len(title) > 100 or not alt or len(alt) > 300:
                error = "Informe um título de até 100 caracteres e um texto alternativo de até 300 caracteres."
            elif has_upload:
                if not mime:
                    error = "Imagem inválida. Envie PNG, JPG ou WebP de até 900 KB. A imagem anterior foi preservada."
                else:
                    try:
                        dimensions, derivatives = optimized_versions(data)
                    except (OSError, ValueError, Image.DecompressionBombError):
                        error = "Não foi possível processar a imagem. A imagem anterior foi preservada."
            if error:
                flash(error, "error")
                return render_template("explanatory_card_settings.html", attempted=request.form), 400
            if derivatives:
                revision = hashlib.sha256(data).hexdigest()
                db().execute("""UPDATE explanatory_cards SET title=?,alt_text=?,image=?,mime_type=?,
                    thumbnail_small=?,thumbnail_large=?,width=?,height=?,revision=? WHERE id=?""",
                    (title, alt, data, mime, *derivatives, *dimensions, revision, card_id))
            else:
                db().execute("UPDATE explanatory_cards SET title=?,alt_text=? WHERE id=?", (title, alt, card_id))
            audit("atualizar_cartao_explicativo", "cartao", card_id)
            db().commit()
            flash("Cartão atualizado com sucesso!", "success")
            return redirect(url_for("explanatory_card_settings"))
        return render_template("explanatory_card_settings.html")

    @app.get("/cartoes-explicativos/<int:card_id>/<variant>")
    def explanatory_card_image(card_id, variant):
        columns = {"original": "image", "celular": "thumbnail_small", "computador": "thumbnail_large"}
        if variant not in columns:
            abort(404)
        card = db().execute(f"SELECT {columns[variant]} AS content,mime_type,revision FROM explanatory_cards WHERE id=?", (card_id,)).fetchone()
        if not card:
            abort(404)
        if request.args.get("v") != card["revision"]:
            return redirect(url_for("explanatory_card_image", card_id=card_id, variant=variant, v=card["revision"]))
        if card["content"] is None:
            # Preserve the exact existing vector illustrations, already lightweight
            # and resolution independent, until an administrator replaces them.
            icon_name = DEFAULT_CARDS[card_id - 1][3]
            data = render_template_string("{% from 'icons.html' import icon %}{{ icon(name) }}", name=icon_name)
            data = data.replace('<svg ', '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" ').replace('currentColor', '#00ced1')
            mime = "image/svg+xml"
        else:
            data = bytes(card["content"])
            mime = card["mime_type"] if variant == "original" else "image/webp"
        response = Response(data, mimetype=mime)
        response.set_etag(card["revision"] + "-" + variant)
        response.headers["Cache-Control"] = "public,max-age=31536000,immutable"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response.make_conditional(request)

    @app.route("/painel/configuracoes/pix", methods=["GET", "POST"])
    @admin_required
    def pix_settings():
        staff = current_staff()
        if staff and staff["role"] != "administrador":
            abort(403)
        setting = dict(db().execute("SELECT pix_receiver_name,pix_key,pix_key_type FROM settings WHERE id=1").fetchone())
        editing = request.args.get("editar") == "1" or request.method == "POST"
        if request.method == "POST":
            setting = {"pix_receiver_name": request.form.get("receiver", "").strip(),
                       "pix_key": request.form.get("pix_key", "").strip(),
                       "pix_key_type": request.form.get("pix_key_type", "")}
            try:
                setting["pix_key"] = validate_pix_settings(setting["pix_receiver_name"], setting["pix_key_type"], setting["pix_key"])
            except ValueError as error:
                flash(str(error), "error")
                return render_template("pix_settings.html", setting=setting, editing=True), 400
            db().execute("UPDATE settings SET pix_receiver_name=?,pix_key=?,pix_key_type=? WHERE id=1",
                         (setting["pix_receiver_name"], setting["pix_key"], setting["pix_key_type"]))
            audit("configurar_pix", "configuracao", 1, "dados do Pix atualizados")
            db().commit()
            flash("Dados do Pix atualizados com sucesso!", "success")
            return redirect(url_for("pix_settings"))
        return render_template("pix_settings.html", setting=setting, editing=editing)

    @app.get("/painel/pacotes/<int:package_id>")
    @login_required
    def package_detail(package_id):
        package = db().execute(f"""SELECT {PACKAGE_COLUMNS_P},c.name client_name,c.public_id client_public_id,c.id owner_id
            FROM packages p JOIN clients c ON c.id=p.client_id WHERE p.id=?""", (package_id,)).fetchone()
        if not package:
            abort(404)
        withdrawal = db().execute(f"""SELECT {PICKUP_COLUMNS_PU},pp.package_public_id,pp.client_id snapshot_client_id,
            pp.client_name snapshot_client_name,pp.client_public_id snapshot_client_public_id,pp.tracking_code snapshot_tracking,
            pp.package_category snapshot_category,pp.notified_at snapshot_notified_at,pp.service_amount,pp.late_days,pp.late_amount,pp.total_amount
            FROM pickup_packages pp JOIN pickups pu ON pu.id=pp.pickup_id WHERE pp.package_id=?""", (package_id,)).fetchone()
        pickup_packages = []
        if withdrawal:
            pickup_packages = db().execute(f"SELECT {PICKUP_PACKAGE_COLUMNS} FROM pickup_packages WHERE pickup_id=? ORDER BY package_id", (withdrawal["id"],)).fetchall()
        return render_template("package_detail.html", package=package, withdrawal=withdrawal, pickup_packages=pickup_packages,
            pickup_success=request.args.get("retirada") == "sucesso")

    @app.get("/cliente/pacotes/<int:package_id>")
    @client_login_required
    def client_package_detail(package_id):
        package = db().execute(f"""SELECT {PACKAGE_COLUMNS_P},c.name client_name,c.public_id client_public_id,c.id owner_id
            FROM packages p JOIN clients c ON c.id=p.client_id WHERE p.id=? AND p.client_id=?""", (package_id, session["client_id"])).fetchone()
        if not package:
            abort(404)
        withdrawal = db().execute(f"""SELECT {PICKUP_COLUMNS_PU},pp.package_public_id,pp.client_id snapshot_client_id,
            pp.client_name snapshot_client_name,pp.client_public_id snapshot_client_public_id,pp.tracking_code snapshot_tracking,
            pp.package_category snapshot_category,pp.notified_at snapshot_notified_at,pp.service_amount,pp.late_days,pp.late_amount,pp.total_amount
            FROM pickup_packages pp JOIN pickups pu ON pu.id=pp.pickup_id WHERE pp.package_id=?""", (package_id,)).fetchone()
        pickup_packages = db().execute(f"SELECT {PICKUP_PACKAGE_COLUMNS} FROM pickup_packages WHERE pickup_id=? AND client_id=? ORDER BY package_id", (withdrawal["id"], session["client_id"])).fetchall() if withdrawal else []
        return render_template("package_detail.html", package=package, withdrawal=withdrawal, pickup_packages=pickup_packages, client_view=True)

    @app.post("/painel/clientes/<int:client_id>/link-senha")
    @login_required
    def password_link(client_id):
        if request.form.get("identity_checked") != "yes" or not db().execute("SELECT 1 FROM clients WHERE id=?", (client_id,)).fetchone():
            abort(400, "Confirme a conferência manual da identidade.")
        raw = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        expires = (now() + timedelta(minutes=app.config["PASSWORD_LINK_MINUTES"])).isoformat()
        db().execute("UPDATE password_tokens SET used_at=? WHERE client_id=? AND used_at IS NULL", (now().isoformat(), client_id))
        db().execute("INSERT INTO password_tokens(client_id,token_hash,expires_at,created_at,created_by) VALUES(?,?,?,?,?)", (client_id, token_hash, expires, now().isoformat(), session["staff"]))
        db().commit()
        link = url_for("set_password", token=raw, _external=True)
        return render_template("password_link.html", link=link, expires=expires)

    @app.route("/definir-senha/<token>", methods=["GET", "POST"])
    def set_password(token):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        row = db().execute("SELECT id,client_id,token_hash,expires_at,created_at,created_by,used_at FROM password_tokens WHERE token_hash=?", (token_hash,)).fetchone()
        valid = row and not row["used_at"] and datetime.fromisoformat(row["expires_at"]) > now()
        if not valid:
            return render_template("set_password.html", invalid=True), 410
        if request.method == "POST":
            password = request.form.get("password", "")
            if len(password) < 8 or password != request.form.get("password_confirm"):
                flash("As senhas devem ser iguais e ter ao menos 8 caracteres.", "error")
                return redirect(request.url)
            db().execute("UPDATE clients SET password_hash=?,session_version=session_version+1 WHERE id=?", (generate_password_hash(password), row["client_id"]))
            db().execute("UPDATE password_tokens SET used_at=? WHERE id=?", (now().isoformat(), row["id"]))
            db().commit()
            flash("Senha definida. Você já pode entrar.", "success")
            return redirect(url_for("client_login"))
        return render_template("set_password.html", invalid=False)

    @app.post("/pacotes")
    @login_required
    def add_package():
        try:
            data, selected_client, numbers, category, price = validate_package_form()
        except ValueError as error:
            saved_form = package_form_data()
            saved_form["has_error"] = True
            session["package_form"] = saved_form
            message = str(error)
            if "Limite de 150" in message:
                message = "Pacote recusado: Limite de 150 cm na soma e 20 kg."
            flash(message, "error")
            return redirect(url_for("dashboard") + "#novo-pacote")
        package_owner=db().execute('SELECT * FROM clients WHERE id=?',(selected_client['id'],)).fetchone()
        requirements=client_actions.blockers(db(),package_owner,now().isoformat())['new_packages']
        if requirements:
            flash('Novas encomendas dependem de regularização: '+ '; '.join(requirements)+'. A retirada de pacotes já recebidos permanece disponível.','error')
            return redirect(url_for('dashboard')+'#novo-pacote')
        width, height, length, weight = numbers
        db().commit()
        db().begin_write()
        plan_service.lock_account(db(), selected_client["id"], now())
        try:
            cur = db().execute("""INSERT INTO packages(client_id, tracking, width, height, length, weight, shelf, category, base_price, status, created_at)
                VALUES(?,?,?,?,?,?,?,?,?,'aguardando_aviso',?) RETURNING id""", (selected_client["id"], data["tracking"].strip(), width, height, length, weight, data["shelf"].strip(), category, price, now().isoformat()))
        except DatabaseIntegrityError:
            db().rollback()
            data["has_error"] = True
            session["package_form"] = data
            flash("Este código de rastreio já está cadastrado. Confira o pacote existente.", "error")
            return redirect(url_for("dashboard") + "#novo-pacote")
        new_package_id = cur.fetchone()["id"]
        package_id = f"PCTPCA-{new_package_id}"
        db().execute("UPDATE packages SET public_id=? WHERE id=?", (package_id, new_package_id))
        covered = plan_service.consume(db(), selected_client["id"], new_package_id, package_id, category, price, now())
        audit("criar", "pacote", new_package_id, package_id)
        db().commit()
        flash(f"{package_id} registrado como {category}." + (" Coberto pelo plano; crédito registrado no recebimento." if covered else " Cobrança avulsa."), "success")
        return redirect(url_for("dashboard"))

    @app.route("/painel/pacotes/<int:package_id>/editar", methods=["GET", "POST"])
    @login_required
    def edit_package(package_id):
        package = db().execute(f"""SELECT {PACKAGE_COLUMNS_P},c.name client_name,c.public_id client_public_id FROM packages p JOIN clients c ON c.id=p.client_id WHERE p.id=?""", (package_id,)).fetchone()
        if not package:
            abort(404)
        if package["status"] != "aguardando_aviso" or db().execute("SELECT 1 FROM pickup_packages WHERE package_id=?", (package_id,)).fetchone():
            flash("Este pacote não pode mais ser editado porque o aviso ou a retirada já foi registrado.", "error")
            return redirect(url_for("package_detail", package_id=package_id))
        form_data = session.pop(f"edit_package_{package_id}", None)
        if request.method == "POST":
            try:
                data, selected_client, numbers, category, price = validate_package_form(package_id)
            except ValueError as error:
                saved_form = package_form_data()
                saved_form["has_error"] = True
                session[f"edit_package_{package_id}"] = saved_form
                message = "Pacote recusado: Limite de 150 cm na soma e 20 kg." if "Limite de 150" in str(error) else str(error)
                flash(message, "error")
                return redirect(url_for("edit_package", package_id=package_id))
            coverage = plan_service.coverage(db(), package_id)
            if coverage:
                if selected_client['id'] != package['client_id'] or category != package['category']:
                    flash('Pacote coberto pelo plano: não é permitido alterar cliente ou categoria, preservando o histórico de créditos.', 'error')
                    return redirect(url_for('edit_package', package_id=package_id))
                price = 0
            width, height, length, weight = numbers
            updated = db().execute("""UPDATE packages SET client_id=?,tracking=?,width=?,height=?,length=?,weight=?,shelf=?,category=?,base_price=? WHERE id=? AND status='aguardando_aviso' AND NOT EXISTS(SELECT 1 FROM pickup_packages WHERE package_id=?)""",
                (selected_client["id"], data["tracking"].strip(), width, height, length, weight, data["shelf"].strip(), category, price, package_id, package_id))
            if updated.rowcount != 1:
                db().rollback()
                flash("O status mudou e o pacote não pode mais ser editado.", "error")
                return redirect(url_for("package_detail", package_id=package_id))
            audit("editar", "pacote", package_id, package["public_id"])
            db().commit()
            flash("Pacote atualizado e categoria recalculada.", "success")
            return redirect(url_for("package_detail", package_id=package_id))
        return render_template("package_edit.html", package=package, form_data=form_data or dict(package))

    @app.route("/painel/pacotes/<int:package_id>/excluir", methods=["GET", "POST"])
    @login_required
    def delete_package(package_id):
        package = db().execute(f"""SELECT {PACKAGE_COLUMNS_P},c.name client_name FROM packages p JOIN clients c ON c.id=p.client_id WHERE p.id=?""", (package_id,)).fetchone()
        if not package:
            abort(404)
        allowed = not plan_service.coverage(db(), package_id) and package["status"] == "aguardando_aviso" and not db().execute("SELECT 1 FROM pickup_packages WHERE package_id=?", (package_id,)).fetchone()
        if request.method == "POST":
            if not allowed:
                flash("Pacotes com crédito do plano, aviso ou retirada não podem ser excluídos; o histórico deve ser preservado.", "error")
                return redirect(url_for("package_detail", package_id=package_id))
            if request.form.get("confirmation") != package["public_id"]:
                flash("Confirmação inválida. Digite o ID do pacote.", "error")
                return redirect(request.url)
            audit("excluir", "pacote", package_id, f"{package['public_id']} · {package['tracking']}")
            deleted = db().execute("DELETE FROM packages WHERE id=? AND status='aguardando_aviso' AND NOT EXISTS(SELECT 1 FROM pickup_packages WHERE package_id=?)", (package_id, package_id))
            if deleted.rowcount != 1:
                db().rollback()
                flash("O status mudou e o pacote não pode mais ser excluído.", "error")
                return redirect(url_for("package_detail", package_id=package_id))
            db().commit()
            flash("Pacote excluído. O cliente e os demais pacotes foram preservados.", "success")
            return redirect(url_for("dashboard"))
        return render_template("package_delete.html", package=package, allowed=allowed)

    @app.post("/pacotes/<int:package_id>/avisar")
    @login_required
    def notify(package_id):
        package = db().execute("SELECT status FROM packages WHERE id=?", (package_id,)).fetchone()
        if not package:
            abort(404)
        if package["status"] == "aguardando_aviso":
            db().execute("UPDATE packages SET status='aguardando_retirada', notified_at=? WHERE id=?", (now().isoformat(), package_id))
            db().commit()
        flash("Aviso confirmado. O prazo foi iniciado sem alterar avisos anteriores.", "success")
        return redirect(url_for("dashboard"))

    @app.post("/painel/retirada/resumo")
    @login_required
    def pickup_summary():
        try:
            ids = list(dict.fromkeys(int(value) for value in request.form.getlist("package_ids")))
        except ValueError:
            return {"error": "Seleção inválida."}, 400
        if not ids:
            return {"error": "Selecione ao menos um pacote."}, 400
        placeholders = ",".join("?" * len(ids))
        packages = db().execute(f"SELECT {PACKAGE_COLUMNS} FROM packages WHERE id IN ({placeholders}) AND status='aguardando_retirada'", ids).fetchall()
        if len(packages) != len(ids):
            return {"error": "Há pacote indisponível para retirada."}, 400
        totals = [fees(package) for package in packages]
        package_total = round(sum(item["total"] for item in totals), 2)
        try:
            surcharge_amount, surcharge_reason = validate_surcharge(request.form.get("surcharge_amount", "0"), request.form.get("surcharge_reason", ""))
            discount_type, discount_input, discount_amount, original_total, discounted_total = calculate_discount(
                package_total, request.form.get("discount_amount", ""), request.form.get("discount_percent", ""))
            final_total = (discounted_total + surcharge_amount).quantize(Decimal("0.01"))
        except ValueError as error:
            return {"error": str(error)}, 400
        setting = db().execute("SELECT pix_receiver_name,pix_key,pix_key_type FROM settings WHERE id=1").fetchone()
        receiver = setting["pix_receiver_name"]
        payload = pix_payload(setting["pix_key"], receiver, float(final_total)) if receiver and final_total > 0 else None
        qr_data = qr_code_data_url(payload) if payload else None
        return {"total": float(final_total), "package_total": package_total, "surcharge_amount": float(surcharge_amount),
            "original_total": float(original_total), "discount_amount": float(discount_amount),
            "package_total_display": f"R$ {package_total:.2f}".replace(".", ","),
            "surcharge_display": f"R$ {surcharge_amount:.2f}".replace(".", ","),
            "total_display": f"R$ {final_total:.2f}".replace(".", ","),
            "original_total_display": f"R$ {original_total:.2f}".replace(".", ","),
            "discount_display": f"R$ {discount_amount:.2f}".replace(".", ","),
            "discount_type": discount_type, "discount_input": float(discount_input) if discount_input is not None else None,
            "pix_code": payload, "qr_code": qr_data, "pix_configured": bool(receiver), "pix_zero_total": final_total == 0}

    @app.post("/retiradas")
    @login_required
    def pickup():
        raw_ids = request.form.getlist("package_ids")
        try:
            # O navegador pode enviar o mesmo checkbox mais de uma vez. Remova
            # repetições mantendo a ordem antes de calcular ou persistir valores.
            ids = list(dict.fromkeys(int(value) for value in raw_ids))
        except ValueError:
            flash("A seleção contém um pacote inválido.", "error")
            return redirect(url_for("dashboard"))
        if not ids:
            flash("Selecione ao menos um pacote.", "error")
            return redirect(url_for("dashboard"))
        receiver = request.form.get("receiver_name", "").strip()
        document_type = request.form.get("document_type", "")
        document = request.form.get("receiver_document", "").strip().upper()
        payment_method = request.form.get("payment_method", "")
        signature = request.form.get("pickup_signature", "")
        payment_confirmed = request.form.get("payment_confirmed") == "yes"
        allowed_payment_methods = ("Pix", "Dinheiro")
        if not receiver or not valid_document(document_type, document) or payment_method not in allowed_payment_methods or not payment_confirmed or not signature.startswith("data:image/png;base64,"):
            flash("Informe nome, tipo e número do documento, pagamento confirmado e assinatura.", "error")
            return redirect(url_for("dashboard"))
        placeholders = ",".join("?" * len(ids))
        connection = db()
        try:
            # Serializa a conferência e a gravação para que duas confirmações
            # simultâneas não consigam retirar o mesmo pacote.
            connection.begin_write()
            existing = connection.execute(
                f"SELECT pp.package_id,pp.pickup_id FROM pickup_packages pp WHERE pp.package_id IN ({placeholders}) LIMIT 1",
                ids,
            ).fetchone()
            if existing:
                connection.rollback()
                flash("Este pacote já possui uma retirada registrada. Consulte o comprovante existente abaixo.", "error")
                return redirect(url_for("package_detail", package_id=existing["package_id"]))

            packages = connection.execute(
                f"SELECT {PACKAGE_COLUMNS} FROM packages WHERE id IN ({placeholders}) AND status='aguardando_retirada' ORDER BY id",
                ids,
            ).fetchall()
            if len(packages) != len(ids):
                connection.rollback()
                flash("Retirada bloqueada: há pacote inválido ou que não está aguardando retirada.", "error")
                return redirect(url_for("dashboard"))

            totals = [fees(package) for package in packages]
            try:
                surcharge_amount, surcharge_reason = validate_surcharge(request.form.get("surcharge_amount", "0"), request.form.get("surcharge_reason", ""))
                discount_type, discount_input, discount_amount, original_total, discounted_total = calculate_discount(
                    sum(x["total"] for x in totals), request.form.get("discount_amount", ""), request.form.get("discount_percent", ""))
                final_total = (discounted_total + surcharge_amount).quantize(Decimal("0.01"))
            except ValueError as error:
                connection.rollback()
                flash(str(error), "error")
                return redirect(url_for("dashboard"))
            picked_at = now().isoformat()
            if payment_method == "Pix" and not connection.execute("SELECT pix_receiver_name,pix_key,pix_key_type FROM settings WHERE id=1 AND pix_receiver_name IS NOT NULL AND trim(pix_receiver_name)!=''").fetchone():
                connection.rollback()
                flash("Configure o nome do recebedor Pix antes de confirmar um pagamento Pix.", "error")
                return redirect(url_for("dashboard"))
            pix_setting = connection.execute("SELECT pix_key,pix_receiver_name,pix_key_type FROM settings WHERE id=1").fetchone()
            cur = connection.execute("""INSERT INTO pickups(receiver_name,receiver_document,document_type,signature,staff,picked_at,
                base_total,late_total,total_paid,payment_method,payment_confirmed,payment_confirmed_at,payment_confirmed_by,
                discount_type,discount_input,discount_amount,original_total,final_total,discount_applied_by,
                surcharge_amount,surcharge_reason,surcharge_applied_by)
                VALUES(?,?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?) RETURNING id""",
                (receiver, document, document_type, signature, session["staff"], picked_at, sum(x["base"] for x in totals),
                 sum(x["late_fee"] for x in totals), float(final_total), payment_method, picked_at, session["staff"],
                 discount_type, float(discount_input) if discount_input is not None else None, float(discount_amount),
                 float(original_total), float(final_total), session["staff"], float(surcharge_amount), surcharge_reason, session["staff"]))
            pickup_id = cur.fetchone()["id"]
            connection.execute("UPDATE pickups SET document_brand_version=?,document_logo_present=? WHERE id=?", (BRAND_VERSION, int(ORIGINAL_LOGO.is_file()), pickup_id))
            if payment_method == "Pix":
                connection.execute("UPDATE pickups SET pix_key_snapshot=?,pix_receiver_snapshot=?,pix_key_type_snapshot=? WHERE id=?",
                    (pix_setting["pix_key"],pix_setting["pix_receiver_name"],pix_setting["pix_key_type"],pickup_id))
            for package, total in zip(packages, totals):
                owner = connection.execute("SELECT id,name,public_id FROM clients WHERE id=?", (package["client_id"],)).fetchone()
                connection.execute("""INSERT INTO pickup_packages(
                    pickup_id,package_id,base_amount,late_amount,package_public_id,client_id,client_name,client_public_id,
                    tracking_code,package_category,notified_at,service_amount,late_days,total_amount) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pickup_id, package["id"], total["base"], total["late_fee"], package["public_id"], owner["id"], owner["name"], owner["public_id"], package["tracking"], package["category"], package["notified_at"], total["base"], total["late_days"], total["total"]))
                connection.execute("UPDATE packages SET status='retirado', picked_at=? WHERE id=?", (picked_at, package["id"]))
            connection.commit()
        except DatabaseIntegrityError:
            connection.rollback()
            existing = connection.execute(
                f"SELECT package_id,pickup_id FROM pickup_packages WHERE package_id IN ({placeholders}) LIMIT 1",
                ids,
            ).fetchone()
            if existing:
                flash("Este pacote já possui uma retirada registrada. Consulte o comprovante existente abaixo.", "error")
                return redirect(url_for("package_detail", package_id=existing["package_id"]))
            flash("Não foi possível concluir a retirada. Nenhuma alteração foi salva.", "error")
            return redirect(url_for("dashboard"))
        except Exception:
            connection.rollback()
            raise
        return redirect(url_for("receipt", pickup_id=pickup_id, retirada="sucesso"))

    @app.get("/comprovantes/<int:pickup_id>")
    @login_required
    def receipt(pickup_id):
        pickup = db().execute(f"SELECT {PICKUP_COLUMNS} FROM pickups WHERE id=?", (pickup_id,)).fetchone()
        if not pickup:
            abort(404)
        package = db().execute("SELECT package_id FROM pickup_packages WHERE pickup_id=? ORDER BY package_id LIMIT 1", (pickup_id,)).fetchone()
        return redirect(url_for("package_detail", package_id=package["package_id"], retirada=request.args.get("retirada")))

    @app.get("/comprovantes/<int:pickup_id>/pacotes/<int:package_id>.pdf")
    def receipt_pdf(pickup_id, package_id):
        row = db().execute(f"""SELECT {PICKUP_COLUMNS_PU},pp.package_public_id,pp.client_id snapshot_client_id,p.client_id owner_id,
            pp.client_name snapshot_client_name,pp.client_public_id snapshot_client_public_id,pp.tracking_code snapshot_tracking,
            pp.package_category snapshot_category,pp.notified_at snapshot_notified_at,pp.service_amount,pp.late_days,pp.late_amount,pp.total_amount
            FROM pickup_packages pp JOIN pickups pu ON pu.id=pp.pickup_id JOIN packages p ON p.id=pp.package_id
            WHERE pp.pickup_id=? AND pp.package_id=?""", (pickup_id, package_id)).fetchone()
        if not row:
            abort(404)
        if not session.get("staff") and session.get("client_id") not in (row["snapshot_client_id"], row["owner_id"]):
            abort(403)
        if session.get("staff"):
            receipt_packages = db().execute(f"SELECT {PICKUP_PACKAGE_COLUMNS} FROM pickup_packages WHERE pickup_id=? ORDER BY package_id", (pickup_id,)).fetchall()
        else:
            receipt_packages = db().execute(f"SELECT {PICKUP_PACKAGE_COLUMNS} FROM pickup_packages WHERE pickup_id=? AND client_id=? ORDER BY package_id", (pickup_id, session["client_id"])).fetchall()
        pdf = build_receipt_pdf(row, receipt_packages, ADDRESS)
        filename = f"comprovante-{row['package_public_id'] or package_id}.pdf"
        return send_file(pdf, mimetype="application/pdf", as_attachment=True, download_name=filename)

    with app.app_context():
        init_db(app.config["DATABASE"])
    blog_tools.install(app,db,admin_required,current_staff,now,format_blog_body,validated_image_type)
    install_google_login(app, db, login_blocked, record_login)
    install_regularization(app, db, client_login_required, now, lambda *args: send_code(*args), residential_address_form, valid_residential_address, current_contract)
    client_actions.install(app, db, client_login_required, admin_required, current_staff, now, lambda *args: send_code(*args), lambda: (TERMS_VERSION,CONTRACT_LABEL,terms_text()))
    plan_routes.install(app, db, client_login_required, admin_required, current_staff, now, qr_code_data_url, validated_image_type)
    @app.context_processor
    def action_context():
        if not session.get('client_id'):return {}
        person=db().execute('SELECT * FROM clients WHERE id=?',(session['client_id'],)).fetchone()
        if not person or not person['active'] or person['session_version']!=session.get('client_session_version'):return {}
        items,missing,count=client_actions.summary(db(),person,now().isoformat())
        return {'action_items':[x for x in items if x['state']=='pending'],'action_count':count, 'action_missing':missing,'action_labels':client_actions.ACTIONS,
                'function_blocks':client_actions.blockers(db(),person,now().isoformat(),session.get('client_auth_method')=='google')}
    @app.get('/cliente/dados-de-entrega')
    @client_login_required
    def client_delivery_data():
        person=db().execute('SELECT * FROM clients WHERE id=?',(session['client_id'],)).fetchone()
        restrictions=client_actions.blockers(db(),person,now().isoformat(),session.get('client_auth_method')=='google')['delivery']
        if restrictions:return render_template('action_blocked.html',requirements=restrictions),403
        return render_template('delivery_data.html',client=person)
    return app


def digits(value):
    return "".join(c for c in value if c.isdigit())


def fees(package, today=None):
    return calculate_fees(package["base_price"], package["notified_at"], package["picked_at"], today)


def next_public_code(connection, prefix):
    row = connection.execute("""INSERT INTO id_sequences(prefix,last_value) VALUES(?,1)
        ON CONFLICT(prefix) DO UPDATE SET last_value=id_sequences.last_value+1 RETURNING last_value""", (prefix,)).fetchone()
    return f"{prefix}-{row['last_value']}"


def migrate_client_public_codes(connection):
    """Atualiza o código de uso diário sem alterar documentos históricos."""
    clients = connection.execute("SELECT id,public_id FROM clients WHERE public_id IS NOT NULL AND trim(public_id)!='' ORDER BY id").fetchall()
    connection.execute("UPDATE clients SET contract_public_id=public_id WHERE contract_public_id IS NULL AND public_id IS NOT NULL")
    used_codes = {client["public_id"] for client in clients}
    sequences = connection.execute("SELECT last_value FROM id_sequences WHERE prefix IN ('CLTPCA','CLIPCA')").fetchall()
    maximum = max((row["last_value"] for row in sequences), default=0)
    for client in clients:
        match = re.fullmatch(r"(?:CLTPCA|CLIPCA)-(\d+)", client["public_id"])
        if match:
            maximum = max(maximum, int(match.group(1)))
    for client in clients:
        match = re.fullmatch(r"CLTPCA-(\d+)", client["public_id"])
        if not match:
            continue
        new_code = f"CLIPCA-{match.group(1)}"
        if new_code in used_codes:
            maximum += 1
            new_code = f"CLIPCA-{maximum}"
        connection.execute("UPDATE clients SET legacy_public_id=COALESCE(legacy_public_id,public_id),public_id=? WHERE id=?",
                           (new_code, client["id"]))
        used_codes.add(new_code)
    if maximum:
        connection.execute("""INSERT INTO id_sequences(prefix,last_value) VALUES('CLIPCA',?)
            ON CONFLICT(prefix) DO UPDATE SET last_value=CASE WHEN id_sequences.last_value < ? THEN ? ELSE id_sequences.last_value END""",
                           (maximum, maximum, maximum))


def init_db(database):
    if not is_postgres(database):
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    connection = DatabaseConnection(database)
    id_type = "BIGSERIAL PRIMARY KEY" if connection.postgres else "INTEGER PRIMARY KEY"
    statements = [
        f"CREATE TABLE IF NOT EXISTS clients (id {id_type}, public_id TEXT UNIQUE, name TEXT NOT NULL, cpf TEXT NOT NULL, phone TEXT NOT NULL, email TEXT, terms_version TEXT NOT NULL, terms_text TEXT, signature TEXT NOT NULL, accepted_at TEXT NOT NULL, password_hash TEXT, active INTEGER NOT NULL DEFAULT 1, session_version INTEGER NOT NULL DEFAULT 1, residential_cep TEXT, residential_street TEXT, residential_number TEXT, residential_district TEXT, residential_city TEXT, residential_state TEXT, residential_complement TEXT)",
        f"CREATE TABLE IF NOT EXISTS packages (id {id_type}, public_id TEXT UNIQUE, client_id BIGINT NOT NULL REFERENCES clients(id), tracking TEXT, width DOUBLE PRECISION NOT NULL, height DOUBLE PRECISION NOT NULL, length DOUBLE PRECISION NOT NULL, weight DOUBLE PRECISION NOT NULL, shelf TEXT, category TEXT NOT NULL, base_price DOUBLE PRECISION NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, notified_at TEXT, picked_at TEXT)",
        f"CREATE TABLE IF NOT EXISTS pickups (id {id_type}, receiver_name TEXT NOT NULL, receiver_document TEXT NOT NULL, document_type TEXT, signature TEXT NOT NULL, staff TEXT NOT NULL, picked_at TEXT NOT NULL, base_total DOUBLE PRECISION NOT NULL, late_total DOUBLE PRECISION NOT NULL, total_paid DOUBLE PRECISION NOT NULL, payment_method TEXT, payment_confirmed INTEGER NOT NULL, payment_confirmed_at TEXT, payment_confirmed_by TEXT, discount_type TEXT, discount_input DOUBLE PRECISION, discount_amount DOUBLE PRECISION, original_total DOUBLE PRECISION, final_total DOUBLE PRECISION, discount_applied_by TEXT, surcharge_amount DOUBLE PRECISION, surcharge_reason TEXT, surcharge_applied_by TEXT)",
        "CREATE TABLE IF NOT EXISTS pickup_packages (pickup_id BIGINT REFERENCES pickups(id), package_id BIGINT UNIQUE REFERENCES packages(id), base_amount DOUBLE PRECISION NOT NULL, late_amount DOUBLE PRECISION NOT NULL, package_public_id TEXT, client_id BIGINT, client_name TEXT, client_public_id TEXT, tracking_code TEXT, package_category TEXT, notified_at TEXT, service_amount DOUBLE PRECISION, late_days INTEGER, total_amount DOUBLE PRECISION, PRIMARY KEY(pickup_id, package_id))",
        f"CREATE TABLE IF NOT EXISTS password_tokens (id {id_type}, client_id BIGINT NOT NULL REFERENCES clients(id), token_hash TEXT UNIQUE NOT NULL, expires_at TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL, used_at TEXT)",
        f"CREATE TABLE IF NOT EXISTS login_attempts (id {id_type}, kind TEXT NOT NULL, identifier TEXT NOT NULL, succeeded INTEGER NOT NULL, attempted_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(kind,identifier,attempted_at)",
        f"CREATE TABLE IF NOT EXISTS audit_logs (id {id_type}, actor TEXT NOT NULL, action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id BIGINT, details TEXT, created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), pix_receiver_name TEXT, header_image BYTEA, header_image_mime TEXT)",
        "INSERT INTO settings(id,pix_receiver_name) VALUES(1,'AMOR INFINITO MARKETING E SOLUCOES EMPRESARIAIS') ON CONFLICT(id) DO NOTHING",
        f"CREATE TABLE IF NOT EXISTS blog_posts (id {id_type}, slug TEXT UNIQUE NOT NULL, title TEXT NOT NULL, summary TEXT, body TEXT NOT NULL, cover_image BYTEA, cover_alt TEXT, seo_title TEXT, seo_description TEXT, status TEXT NOT NULL DEFAULT 'draft', published_at TEXT, updated_at TEXT NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS verification_codes (id {id_type}, client_id BIGINT NOT NULL REFERENCES clients(id), purpose TEXT NOT NULL, channel TEXT NOT NULL, destination TEXT NOT NULL, code_hash TEXT NOT NULL, expires_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, sends INTEGER NOT NULL DEFAULT 1, provider_status TEXT, created_at TEXT NOT NULL, used_at TEXT)",
        f"CREATE TABLE IF NOT EXISTS reminder_logs (id {id_type}, package_id BIGINT NOT NULL REFERENCES packages(id), channel TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS site_banners (id {id_type}, image BYTEA NOT NULL, mime_type TEXT NOT NULL, alt_text TEXT NOT NULL, position INTEGER NOT NULL UNIQUE, updated_at TEXT NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS site_photos (id {id_type}, kind TEXT NOT NULL, image BYTEA NOT NULL, mime_type TEXT NOT NULL, alt_text TEXT NOT NULL, position INTEGER NOT NULL UNIQUE, updated_at TEXT NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS site_themes (id {id_type}, name TEXT NOT NULL, colors TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS staff_users (id {id_type}, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL, is_primary INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, can_settings INTEGER NOT NULL DEFAULT 0, can_users INTEGER NOT NULL DEFAULT 0, can_clients INTEGER NOT NULL DEFAULT 1, can_packages INTEGER NOT NULL DEFAULT 1, can_pickups INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, session_version INTEGER NOT NULL DEFAULT 1, deleted_at TEXT, last_login_at TEXT)",
        f"CREATE TABLE IF NOT EXISTS staff_recovery_codes (id {id_type}, staff_user_id BIGINT NOT NULL REFERENCES staff_users(id), code_hash TEXT NOT NULL, expires_at TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, provider_status TEXT, created_at TEXT NOT NULL, used_at TEXT)",
        "CREATE TABLE IF NOT EXISTS id_sequences (prefix TEXT PRIMARY KEY, last_value BIGINT NOT NULL)",
        f"CREATE TABLE IF NOT EXISTS analytics_events (id {id_type}, visitor_id TEXT NOT NULL, event_type TEXT NOT NULL, path TEXT, origin TEXT, device TEXT, created_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_analytics_events_created ON analytics_events(created_at,event_type)",
    ]
    for statement in statements:
        connection.execute(statement)
    connection.execute("""CREATE TABLE IF NOT EXISTS explanatory_cards (
        id INTEGER PRIMARY KEY CHECK(id BETWEEN 1 AND 4), title TEXT NOT NULL, alt_text TEXT NOT NULL,
        image BYTEA, mime_type TEXT, thumbnail_small BYTEA, thumbnail_large BYTEA,
        width INTEGER NOT NULL DEFAULT 24, height INTEGER NOT NULL DEFAULT 24,
        revision TEXT NOT NULL DEFAULT 'initial-v1')""")
    for card_id, title, alt, _ in DEFAULT_CARDS:
        connection.execute("INSERT INTO explanatory_cards(id,title,alt_text) VALUES(?,?,?) ON CONFLICT(id) DO NOTHING", (card_id,title,alt))
    connection.execute("""CREATE TABLE IF NOT EXISTS google_oauth_flows (
        state_hash TEXT PRIMARY KEY, nonce TEXT NOT NULL, pkce_verifier TEXT NOT NULL, expires_at BIGINT NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS google_client_identities (
        google_sub TEXT PRIMARY KEY, client_id BIGINT NOT NULL UNIQUE REFERENCES clients(id) ON DELETE CASCADE,
        email_at_link TEXT NOT NULL, linked_at BIGINT NOT NULL)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS google_login_pending (
        id_hash TEXT PRIMARY KEY, google_sub TEXT NOT NULL, email TEXT NOT NULL, client_id BIGINT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
        session_version INTEGER NOT NULL, expires_at BIGINT NOT NULL)""")
    ensure_columns(connection,'blog_posts',{'layout':"TEXT NOT NULL DEFAULT 'legacy'",'toc_enabled':'INTEGER NOT NULL DEFAULT 0'})
    blog_tools.schema(connection)
    columns = table_columns(connection, "clients")
    if "password_hash" not in columns:
        connection.execute("ALTER TABLE clients ADD COLUMN password_hash TEXT")
    if "terms_text" not in columns:
        connection.execute("ALTER TABLE clients ADD COLUMN terms_text TEXT")
    ensure_columns(connection, "clients", {"active": "INTEGER NOT NULL DEFAULT 1", "session_version": "INTEGER NOT NULL DEFAULT 1",
        "residential_cep": "TEXT", "residential_street": "TEXT", "residential_number": "TEXT",
        "residential_district": "TEXT", "residential_city": "TEXT", "residential_state": "TEXT",
        "residential_complement": "TEXT", "email": "TEXT", "contract_id": "TEXT", "contract_hash": "TEXT",
        "confirmation_channel": "TEXT", "confirmation_destination": "TEXT", "confirmation_validated_at": "TEXT",
        "accept_ip": "TEXT", "accept_user_agent": "TEXT",
        "legacy_public_id": "TEXT", "contract_public_id": "TEXT", "document_brand_version": "TEXT", "document_logo_present": "INTEGER NOT NULL DEFAULT 0"})
    ensure_columns(connection, 'clients', {'terms_display_label':'TEXT','contract_revision_id':'TEXT'})
    snapshot_columns=','.join(name + (' INTEGER' if name=='document_logo_present' else ' TEXT') for name in CONTRACT_FIELDS)
    connection.execute(f"""CREATE TABLE IF NOT EXISTS client_contract_records (
        id {id_type}, client_id BIGINT NOT NULL REFERENCES clients(id), source TEXT NOT NULL,
        recorded_at TEXT NOT NULL,{snapshot_columns})""")
    ensure_columns(connection, "client_contract_records", {"contract_revision_id":"TEXT"})
    connection.execute("CREATE INDEX IF NOT EXISTS idx_client_contract_owner ON client_contract_records(client_id,id)")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_client_original_contract ON client_contract_records(client_id) WHERE source='legacy'")
    connection.execute(f"""CREATE TABLE IF NOT EXISTS client_contract_email_confirmations (
        id {id_type}, client_id BIGINT NOT NULL REFERENCES clients(id), acceptance_key TEXT NOT NULL,
        email TEXT NOT NULL,confirmed_at TEXT NOT NULL,accept_ip TEXT,accept_user_agent TEXT)""")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_client_confirmation_owner ON client_contract_email_confirmations(client_id,acceptance_key)")
    connection.execute("""CREATE TABLE IF NOT EXISTS client_regularization_codes (
        client_id BIGINT PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE, scope_hash TEXT NOT NULL,
        code_hash TEXT NOT NULL,expires_at BIGINT NOT NULL,attempts INTEGER NOT NULL,sent_at BIGINT NOT NULL,sends INTEGER NOT NULL,window_start BIGINT NOT NULL)""")
    client_actions.schema(connection,id_type)
    plan_service.schema(connection,id_type)
    ensure_columns(connection, "pickups", {"payment_method": "TEXT", "document_type": "TEXT", "payment_confirmed_at": "TEXT", "payment_confirmed_by": "TEXT",
        "discount_type": "TEXT", "discount_input": "REAL", "discount_amount": "REAL", "original_total": "REAL",
        "final_total": "REAL", "discount_applied_by": "TEXT", "surcharge_amount": "REAL", "surcharge_reason": "TEXT",
        "surcharge_applied_by": "TEXT", "pix_key_snapshot": "TEXT", "pix_receiver_snapshot": "TEXT", "pix_key_type_snapshot": "TEXT", "document_brand_version": "TEXT", "document_logo_present": "INTEGER NOT NULL DEFAULT 0"})
    ensure_columns(connection, "pickup_packages", {
        "package_public_id": "TEXT", "client_id": "INTEGER", "client_name": "TEXT", "client_public_id": "TEXT",
        "tracking_code": "TEXT", "package_category": "TEXT", "notified_at": "TEXT",
        "service_amount": "REAL", "late_days": "INTEGER", "total_amount": "REAL",
    })
    ensure_columns(connection, "settings", {"header_image": "BYTEA" if connection.postgres else "BLOB", "header_image_mime": "TEXT", "active_theme_id": "BIGINT", "legacy_staff_active": "INTEGER NOT NULL DEFAULT 1",
        "main_banner_image": "BYTEA" if connection.postgres else "BLOB", "main_banner_mime": "TEXT", "main_banner_updated_at": "TEXT"})
    ensure_columns(connection, "staff_users", {"public_id":"TEXT", "can_clients": "INTEGER NOT NULL DEFAULT 1", "can_packages": "INTEGER NOT NULL DEFAULT 1", "can_pickups": "INTEGER NOT NULL DEFAULT 1", "session_version":"INTEGER NOT NULL DEFAULT 1", "deleted_at":"TEXT", "last_login_at":"TEXT"})
    ensure_columns(connection, "packages", {"reminder_sent_at": "TEXT", "forwarded_at": "TEXT"})
    duplicate_tracking = connection.execute("""SELECT 1 FROM packages WHERE tracking IS NOT NULL AND trim(tracking)!=''
        GROUP BY UPPER(trim(tracking)) HAVING COUNT(*)>1 LIMIT 1""").fetchone()
    if not duplicate_tracking:
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_packages_tracking ON packages(UPPER(trim(tracking)))")
    # Migração não destrutiva do endereço antigo; a rota antiga permanece com redirecionamento 301.
    connection.execute("UPDATE blog_posts SET slug='como-funciona-me-guarde-ponto-de-coleta-adelinha' WHERE slug='como-funciona-guarda-aqui-adelinha' AND NOT EXISTS(SELECT 1 FROM blog_posts WHERE slug='como-funciona-me-guarde-ponto-de-coleta-adelinha')")
    initial_posts = [
        ("receber-compras-online-em-franca", "Como receber compras online em Franca sem estar em casa", "Conheça uma alternativa para receber encomendas fora de casa.", "O Me Guarde do Ponto de Coleta Adelinha recebe e armazena seus pacotes para retirada no Jardim Adelinha. Cadastre-se, use seu nome e código na compra e aguarde o aviso de chegada."),
        ("como-funciona-me-guarde-ponto-de-coleta-adelinha", "Como funciona o Me Guarde do Ponto de Coleta Adelinha", "Cadastro, aviso, prazo e retirada explicados.", "## Como funciona\n\nApós o cadastro e assinatura do contrato, use o endereço do Ponto de Coleta Adelinha no Jardim Adelinha, Franca SP, junto do seu código.\n\n- Cadastre-se e copie o destinatário.\n- Aguarde o aviso de chegada.\n- Retire e pague por Pix ou dinheiro.\n\n## Tamanhos, prazo e cuidados\n\nPacotes pequenos custam R$ 4,90 e grandes R$ 9,90. O prazo sem adicional é de quatro dias corridos. O Me Guarde recebe e guarda encomendas; não se apresenta como Agência Correios, Agência Shopee ou ponto credenciado de postagem de plataformas."),
        ("endereco-para-receber-compras-adelinha", "Qual endereço usar para receber suas compras no Ponto de Coleta Adelinha", "Veja como identificar corretamente sua entrega.", "Use Rua Afonso Borges de Freitas, 795, Jardim Adelinha, Franca – SP, com o CEP exibido no sistema, além do seu nome completo e código de cliente."),
        ("quanto-custa-guardar-encomenda", "Quanto custa receber e guardar uma encomenda", "Entenda valores, limites e atraso.", "Pequeno: soma até 80 cm e 10 kg, R$ 4,90. Grande: soma até 150 cm e 20 kg, R$ 9,90. Desde o quinto dia há R$ 0,50 por dia e pacote."),
        ("retirada-de-pacotes-por-outra-pessoa", "Como funciona a retirada de pacotes por outra pessoa", "Documentos e autorização para retirada por terceiro.", "Outra pessoa pode retirar com autorização verificável do titular, documento oficial próprio com foto e assinatura no comprovante de retirada."),
    ]
    published = datetime.now(TZ).isoformat()
    for slug, title, summary, body in initial_posts:
        connection.execute("INSERT INTO blog_posts(slug,title,summary,body,cover_alt,seo_title,seo_description,status,published_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(slug) DO NOTHING",
            (slug,title,summary,body,"",title,summary,"published",published,published))
    migrate_client_public_codes(connection)
    role_prefix={"administrador":"ADMPCA","gerente":"GERPCA","atendente":"ATEPCA"}
    # Sincroniza as sequências com códigos já existentes antes de gerar novos.
    # Assim uma migração nunca reutiliza números, mesmo que a tabela de sequência
    # seja criada depois das contas.
    existing_staff=connection.execute("SELECT public_id FROM staff_users WHERE public_id IS NOT NULL AND trim(public_id)!=''").fetchall()
    maxima={prefix:0 for prefix in role_prefix.values()}
    for item in existing_staff:
        match=re.fullmatch(r"(ADMPCA|GERPCA|ATEPCA)-(\d+)",item["public_id"] or "")
        if match: maxima[match.group(1)]=max(maxima[match.group(1)],int(match.group(2)))
    for prefix,maximum in maxima.items():
        if maximum:
            connection.execute("INSERT INTO id_sequences(prefix,last_value) VALUES(?,?) ON CONFLICT(prefix) DO UPDATE SET last_value=CASE WHEN id_sequences.last_value < ? THEN ? ELSE id_sequences.last_value END",(prefix,maximum,maximum,maximum))
    staff_rows=connection.execute("SELECT id,role FROM staff_users WHERE public_id IS NULL OR trim(public_id)='' ORDER BY id").fetchall()
    for staff in staff_rows:
        prefix=role_prefix.get(staff["role"],"ATEPCA")
        connection.execute("UPDATE staff_users SET public_id=? WHERE id=?",(next_public_code(connection,prefix),staff["id"]))
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_staff_users_public_id ON staff_users(public_id)")
    ensure_columns(connection, "settings", {"pix_key": "TEXT", "pix_key_type": "TEXT"})
    connection.execute("UPDATE settings SET pix_key=? WHERE pix_key IS NULL", (PIX_KEY,))
    connection.execute("UPDATE settings SET pix_key_type='CNPJ' WHERE pix_key_type IS NULL")
    # Snapshot dos comprovantes anteriores antes de permitir a primeira edição.
    connection.execute("UPDATE pickups SET pix_key_snapshot=?,pix_receiver_snapshot=?,pix_key_type_snapshot='CNPJ' WHERE payment_method='Pix' AND pix_key_snapshot IS NULL",
                       (PIX_KEY, PIX_RECEIVER_NAME))
    if "main_banner_position" not in table_columns(connection, "settings"):
        ensure_columns(connection, "settings", {"main_banner_position": "INTEGER NOT NULL DEFAULT 1",
            "main_banner_active": "INTEGER NOT NULL DEFAULT 1", "main_banner_alt": "TEXT NOT NULL DEFAULT 'ME GUARDE'"})
        connection.execute("UPDATE settings SET main_banner_alt=? WHERE id=1", ("ME GUARDE — COMPROU? DEIXA COM A GENTE.",))
        connection.execute("UPDATE site_banners SET position=-position")
        connection.execute("UPDATE site_banners SET position=1-position")
    if "active" not in table_columns(connection, "site_banners"):
        ensure_columns(connection, "site_banners", {"active": "INTEGER NOT NULL DEFAULT 1"})
        connection.execute("UPDATE site_banners SET active=0 WHERE position>3")
    connection.commit()
    connection.close()


def ensure_columns(connection, table, definitions):
    existing = table_columns(connection, table)
    for name, data_type in definitions.items():
        if name not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {data_type}")


def table_columns(connection, table):
    if connection.postgres:
        rows = connection.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?", (table,)).fetchall()
        return {row["column_name"] for row in rows}
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def display(value, money=False):
    if value is None or value == "":
        return "Não registrado"
    return f"R$ {float(value):.2f}".replace(".", ",") if money else str(value)


def valid_document(document_type, number):
    if document_type == "CPF":
        return len(digits(number)) == 11
    if document_type == "RG":
        return bool(re.fullmatch(r"[A-Z0-9.\-]{5,20}", number))
    if document_type == "CNH":
        return len(digits(number)) == 11
    return False


def qr_code_data_url(payload):
    image = qrcode.make(payload)
    output = BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=os.environ.get("FLASK_DEBUG") == "1")
