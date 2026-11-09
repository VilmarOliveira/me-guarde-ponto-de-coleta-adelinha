"""Read-only Fast inventory and optional private backup. Deliberately no cleanup/apply command."""

import argparse
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core import TZ
from database import DatabaseConnection, is_postgres
from fastflow_gateway import Settings

FINANCIAL_TABLES = (
    "plan_orders",
    "plan_cycles",
    "plan_credit_ledger",
    "plan_admin_decisions",
    "pickup_payment_orders",
    "pickup_payment_packages",
    "pickup_payment_reservations",
    "package_prepayments",
    "pickups",
    "pickup_packages",
    "clients",
    "packages",
)
OPEN = (
    "creating",
    "uncertain",
    "pending",
    "approved",
    "under_review",
    "cancel_requested",
)


def inventory(db, config, stamp=None):
    stamp = stamp or datetime.now(TZ)
    counts = {
        table: db.execute("SELECT COUNT(*) n FROM " + table).fetchone()["n"]
        for table in FINANCIAL_TABLES
    }
    orders = []
    for table, kind in (("plan_orders", "plan"), ("pickup_payment_orders", "packages")):
        for source in db.execute("SELECT * FROM " + table).fetchall():
            row = dict(source)
            method = row.get("local_method") or (
                "Pix Fast" if row.get("method") == "pix" else ""
            )
            if method != "Pix Fast":
                continue
            paid = bool(
                row.get("paid_at")
                or row.get("manual_paid_at")
                or row.get("delivered_at")
            )
            reason = []
            if row["state"] in OPEN:
                reason.append("open_or_uncertain")
            if row.get("error_code"):
                reason.append("provider_or_validation_error")
            if str(row.get("transaction_id")) == "127471":
                reason.append("reported_403_127471")
            if row.get("transaction_id") and not paid:
                reason.append("unpaid_external_transaction_requires_confirmation")
            if reason:
                orders.append(
                    {
                        "kind": kind,
                        "order_id": row["id"],
                        "client_id": row["client_id"],
                        "transaction_id": row.get("transaction_id"),
                        "local_state": row["state"],
                        "amount_cents": row["amount_cents"],
                        "error_code": row.get("error_code"),
                        "archived": bool(row.get("archived_at")),
                        "payment_evidence_present": paid,
                        "reasons": reason,
                        "remote_status": "not_queried",
                        "action": "preserve_and_reconcile_manually",
                    }
                )
    # Only explicit detached tests qualify. Unknown markers and any transaction linkage are preserved.
    tests = [
        row["id"]
        for row in db.execute(
            "SELECT id FROM plan_payment_events WHERE order_id IS NULL AND transaction_id IS NULL AND event_type IN ('test','integration.test','transaction.test') AND outcome IN ('processed','ignored','test')"
        ).fetchall()
    ]
    cutoff = (stamp - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M")
    budgets = [
        {"minute": r["minute"], "endpoint": r["endpoint"]}
        for r in db.execute(
            "SELECT minute,endpoint FROM plan_api_budget WHERE minute<?", (cutoff,)
        ).fetchall()
    ]
    settings = Settings.from_config(config)
    event_counts = {
        table: db.execute("SELECT COUNT(*) n FROM " + table).fetchone()["n"]
        for table in ("plan_payment_events", "pickup_payment_events", "audit_logs")
    }
    return {
        "mode": "review_only_no_deletions",
        "generated_at": stamp.isoformat(),
        "configuration": {
            "ready": settings.ready,
            "api_key_present": bool(settings.api_key),
            "webhook_secret_present": bool(settings.webhook_secret),
            "new_charges_enabled": settings.new_charges_enabled,
        },
        "preserved_tables": counts,
        "preserved_event_counts": event_counts,
        "charges_for_review": orders,
        "state_counts": dict(Counter(o["local_state"] for o in orders)),
        "proposed_cleanup": {
            "detached_explicit_test_event_ids": tests,
            "api_budget_older_than_24h": budgets,
        },
        "never_clean": [
            "financial tables and receipts",
            "linked webhook events and pending events",
            "reservations and deduplication keys",
            "financial audit logs",
            "unknown or unconfirmed charges, including 127471",
        ],
        "requires_approval": True,
        "bank_qr_acceptance": "not_verified",
        "backup": None,
    }


def private_file(path, data):
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
    ) as handle:
        handle.write(data)


def backup_database(db, database, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / (
        "fast-before-review-"
        + uuid.uuid4().hex
        + (".dump" if db.postgres else ".sqlite")
    )
    private_file(target, b"")
    try:
        if db.postgres:
            from psycopg.conninfo import conninfo_to_dict, make_conninfo

            params = conninfo_to_dict(database)
            env = dict(os.environ)
            for key, variable in (
                ("password", "PGPASSWORD"),
                ("sslpassword", "PGSSLPASSWORD"),
            ):
                if key in params:
                    env[variable] = params.pop(key)
            subprocess.run(
                [
                    "pg_dump",
                    "--dbname",
                    make_conninfo(**params),
                    "--format=custom",
                    "--no-owner",
                    "--no-acl",
                    "--file",
                    str(target),
                ],
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            dest = sqlite3.connect(target)
            try:
                db.connection.backup(dest)
            finally:
                dest.close()
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return {
            "path": str(target),
            "sha256": digest.hexdigest(),
            "bytes": target.stat().st_size,
            "restore_tested": False,
        }
    except Exception:
        # Leave partial files for inspection; never claim a completed backup.
        raise RuntimeError(
            "Backup não concluído. Nada foi limpo; confira o ambiente e as permissões."
        ) from None


def main():
    parser = argparse.ArgumentParser(
        description="Inventário somente leitura e backup privado; não altera configurações, não consulta a Fast e não limpa dados."
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    database = (
        os.getenv("DATABASE_URL")
        or os.getenv("DATABASE")
        or str(ROOT / "data/adelinha.db")
    )
    if args.output.exists():
        parser.error("O relatório já existe; escolha outro arquivo.")
    if not is_postgres(database) and not Path(database).is_file():
        parser.error("Banco local não encontrado; nenhum banco foi criado.")
    db = DatabaseConnection(database)
    try:
        if db.postgres:
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        else:
            db.execute("PRAGMA query_only=ON")
        backup = (
            backup_database(db, database, args.backup_dir) if args.backup_dir else None
        )
        if not db.postgres:
            db.execute("BEGIN")
        report = inventory(db, os.environ)
        report["backup"] = backup
        private_file(
            args.output, json.dumps(report, ensure_ascii=False, indent=2).encode()
        )
        print(
            "Relatório privado criado para revisão. Nenhuma cobrança, configuração ou registro foi alterado."
        )
    except Exception:
        print(
            "Revisão não concluída. Nenhuma limpeza foi executada. Confira o banco, pg_dump e os caminhos de saída.",
            file=sys.stderr,
        )
        return 1
    finally:
        db.rollback()
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
