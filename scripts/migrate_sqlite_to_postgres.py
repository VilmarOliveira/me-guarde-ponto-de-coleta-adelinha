"""Copia dados do SQLite local para PostgreSQL sem apagar ou sobrescrever linhas.

Uso: defina DATABASE_URL e execute este arquivo passando opcionalmente o caminho
do SQLite. Faça backup antes. Conflitos de chave são mantidos no destino.
"""

import os
import json
from payment_policies import defaults as payment_defaults
import sqlite3
import sys
from pathlib import Path

from app import DatabaseConnection, init_db

TABLES = (
    "clients",
    "packages",
    "pickups",
    "pickup_packages",
    "password_tokens",
    "login_attempts",
    "audit_logs",
    "settings",
    "blog_posts",
    "blog_icon_assets",
    "blog_icons",
    "verification_codes",
    "reminder_logs",
    "site_banners",
    "site_photos",
    "site_themes",
    "staff_users",
    "staff_recovery_codes",
    "id_sequences",
    "analytics_events",
    "plan_api_budget",
    "plan_accounts",
    "plan_orders",
    "plan_cycles",
    "plan_credit_ledger",
    "plan_payment_events",
    "plan_reminders",
    "plan_admin_decisions",
    "pickup_payment_orders",
    "pickup_payment_packages",
    "pickup_payment_reservations",
    "pickup_payment_events",
    "package_prepayments",
    "customer_payment_policies",
    "preserved_plan_documents",
    "system_migrations",
)


def migrate(source_path, database_url):
    init_db(database_url)
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    target = DatabaseConnection(database_url)
    try:
        for table in TABLES:
            source_columns = [
                row[1] for row in source.execute(f"PRAGMA table_info({table})")
            ]
            if not source_columns:
                continue
            target_columns = {
                row["column_name"]
                for row in target.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=?",
                    (table,),
                ).fetchall()
            }
            columns = [column for column in source_columns if column in target_columns]
            placeholders = ",".join("?" for _ in columns)
            column_sql = ",".join(columns)
            for row in source.execute(f"SELECT {column_sql} FROM {table}"):
                if table == "settings" and "monthly_price_cents" in columns:
                    existing = target.execute(
                        "SELECT monthly_price_cents FROM settings WHERE id=?",
                        (row["id"],),
                    ).fetchone()
                    if existing and existing["monthly_price_cents"] == 3390:
                        target.execute(
                            "UPDATE settings SET monthly_price_cents=? WHERE id=?",
                            (row["monthly_price_cents"], row["id"]),
                        )
                    elif (
                        existing
                        and existing["monthly_price_cents"]
                        != row["monthly_price_cents"]
                    ):
                        raise ValueError(
                            "Mensalidade diferente no destino. Confira o valor antes de migrar."
                        )
                if table == "customer_payment_policies":
                    existing = target.execute(
                        "SELECT rules FROM customer_payment_policies WHERE scope=?",
                        (row["scope"],),
                    ).fetchone()
                    if existing and json.loads(existing["rules"]) == payment_defaults():
                        target.execute(
                            "UPDATE customer_payment_policies SET rules=? WHERE scope=?",
                            (row["rules"], row["scope"]),
                        )
                    elif existing and json.loads(existing["rules"]) != json.loads(
                        row["rules"]
                    ):
                        raise ValueError(
                            "Configurações de pagamento diferentes no destino. Confira as três abas antes de migrar."
                        )
                target.execute(
                    f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                    tuple(row[column] for column in columns),
                )
        for table in (
            "clients",
            "packages",
            "pickups",
            "password_tokens",
            "login_attempts",
            "audit_logs",
            "blog_posts",
            "verification_codes",
            "reminder_logs",
            "site_banners",
            "site_photos",
            "site_themes",
            "staff_users",
            "staff_recovery_codes",
            "plan_credit_ledger",
            "plan_payment_events",
            "pickup_payment_events",
        ):
            target.execute(
                "SELECT setval(pg_get_serial_sequence(?, 'id'), COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM "
                + table,
                (table,),
            )
        target.commit()
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "Defina DATABASE_URL com a conexão do Neon antes de continuar."
        )
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/adelinha.db")
    if not path.is_file():
        raise SystemExit(f"Banco SQLite não encontrado: {path}")
    migrate(path, url)
    print("Migração concluída sem apagar registros existentes no PostgreSQL.")
