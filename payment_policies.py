"""Independent customer payment routing. Never changes the amount due."""

import json
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from core import TZ
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from fastflow_gateway import Settings
from core import pix_payload

SCOPES = {
    "advance": "Pagamento antecipado",
    "subscription": "Contratação do plano",
    "renewal": "Renovação do plano",
}
METHODS = ("Pix Fast", "Pix AI", "Dinheiro")


def defaults():
    rules = [
        dict(
            method=m,
            enabled=True,
            priority=i + 1,
            minimum=400 if i == 0 else 0,
            maximum=100000 if i == 0 else 10000000,
            title=(
                "Pagar em dinheiro no ponto"
                if i == 2
                else "Gerar Pix Fast" if i == 0 else "Pagar com Pix AI"
            ),
            text=(
                "Confirmação automática pela Fast."
                if i == 0
                else "Pagamento sujeito à conferência da equipe."
            ),
        )
        for i, m in enumerate(METHODS)
    ]
    return rules


def schema(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS customer_payment_policies (scope TEXT PRIMARY KEY, rules TEXT NOT NULL)"
    )
    for scope in SCOPES:
        rules = defaults()
        db.execute(
            "INSERT INTO customer_payment_policies(scope,rules) VALUES(?,?) ON CONFLICT DO NOTHING",
            (scope, json.dumps(rules, ensure_ascii=False)),
        )


def rules(db, scope):
    if scope not in SCOPES:
        raise ValueError("Tela de pagamento inválida.")
    return json.loads(
        db.execute(
            "SELECT rules FROM customer_payment_policies WHERE scope=?", (scope,)
        ).fetchone()["rules"]
    )


def select(db, app, scope, cents, configured=None, client_id=None):
    if cents == 0:
        return {"method": "zero", "title": "Nada a pagar", "text": ""}
    failed_fast = False
    if client_id is not None:
        table = "pickup_payment_orders" if scope == "advance" else "plan_orders"
        failure = db.execute(
            "SELECT policy_snapshot FROM "
            + table
            + " WHERE client_id=? AND state='failed' AND transaction_id IS NULL AND local_method='Pix Fast' AND created_at>=? ORDER BY created_at DESC LIMIT 1",
            (client_id, (datetime.now(TZ) - timedelta(minutes=5)).isoformat()),
        ).fetchone()
        if failure and failure["policy_snapshot"]:
            failed_fast = json.loads(failure["policy_snapshot"]).get("scope") == scope
    setting = db.execute(
        "SELECT pix_key,pix_key_type,pix_receiver_name FROM settings WHERE id=1"
    ).fetchone()
    for rule in sorted(
        configured if configured is not None else rules(db, scope),
        key=lambda x: x["priority"],
    ):
        if not rule["enabled"]:
            continue
        method = rule["method"]
        if method != "Dinheiro" and not rule["minimum"] <= cents <= rule["maximum"]:
            continue
        if method == "Pix Fast" and failed_fast:
            continue
        if method == "Pix Fast" and not (
            Settings.from_config(app.config).ready and 400 <= cents <= 500000
        ):
            continue
        if method == "Pix AI" and not (
            setting["pix_key"]
            and setting["pix_key_type"] == "CNPJ"
            and setting["pix_receiver_name"]
        ):
            continue
        return {**rule, "scope": scope}
    return None


def local_code(db, method, cents):
    if method != "Pix AI":
        return None
    row = db.execute(
        "SELECT pix_key,pix_receiver_name FROM settings WHERE id=1"
    ).fetchone()
    return pix_payload(row["pix_key"], row["pix_receiver_name"], cents / 100)


def money(value):
    try:
        number = Decimal(str(value).replace(",", "."))
        if (
            not number.is_finite()
            or number < 0
            or number > 100000
            or number.as_tuple().exponent < -2
        ):
            raise ValueError()
        return int(number * 100)
    except (InvalidOperation, ValueError):
        raise ValueError(
            "Informe valores entre R$ 0,00 e R$ 100.000,00, com até duas casas decimais."
        )


def install(app, db, admin_required, current_staff):
    bp = Blueprint("payment_policies", __name__)

    @bp.route("/painel/configuracoes/pagamentos-cliente", methods=["GET", "POST"])
    @admin_required
    def settings():
        actor = current_staff()
        if actor and actor["role"] != "administrador":
            abort(403)
        scope = request.values.get("aba", "advance")
        if scope not in SCOPES:
            abort(404)
        items = rules(db(), scope)
        error = None
        preview = None
        if request.method == "POST":
            try:
                items = []
                for i, m in enumerate(METHODS):
                    minimum = money(request.form.get(f"min_{i}", "0"))
                    maximum = money(request.form.get(f"max_{i}", "0"))
                    if minimum > maximum:
                        raise ValueError("O mínimo não pode ser maior que o máximo.")
                    priority = int(request.form.get(f"priority_{i}", "0"))
                    title = request.form.get(f"title_{i}", "").strip()
                    text = request.form.get(f"text_{i}", "").strip()
                    if (
                        priority not in (1, 2, 3)
                        or not title
                        or len(title) > 100
                        or len(text) > 1000
                    ):
                        raise ValueError(
                            "Confira prioridade e textos (título até 100 e instruções até 1.000 caracteres)."
                        )
                    items.append(
                        dict(
                            method=m,
                            enabled=request.form.get(f"enabled_{i}") == "yes",
                            priority=priority,
                            minimum=minimum,
                            maximum=maximum,
                            title=title,
                            text=text,
                        )
                    )
                if len(set(x["priority"] for x in items)) != 3:
                    raise ValueError("Use prioridades diferentes: 1, 2 e 3.")
                if request.form.get("action") == "preview":
                    preview = select(
                        db(),
                        app,
                        scope,
                        money(request.form.get("test_value", "0")),
                        items,
                    ) or {
                        "title": "Nenhuma forma elegível. Procure o ponto.",
                        "text": "",
                    }
                else:
                    db().execute(
                        "UPDATE customer_payment_policies SET rules=? WHERE scope=?",
                        (json.dumps(items, ensure_ascii=False), scope),
                    )
                    db().commit()
                    flash("Configurações de pagamento atualizadas.", "success")
                    return redirect(url_for("payment_policies.settings", aba=scope))
            except (ValueError, TypeError) as exc:
                error = str(exc)
        return render_template(
            "customer_payment_settings.html",
            scopes=SCOPES,
            scope=scope,
            items=items,
            error=error,
            preview=preview,
        )

    app.register_blueprint(bp)
