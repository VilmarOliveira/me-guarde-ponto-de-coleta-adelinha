"""Run with the same environment and Python as ponto-coleta.service."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime
from app import create_app
from core import TZ
from database import DatabaseConnection
from notifications import send_email
from plan_jobs import reconcile, reminders


def main():
    parser = argparse.ArgumentParser(
        description="Reconciliação sem criar cobranças e lembretes do plano."
    )
    parser.add_argument("task", choices=("reconcile", "reminders"))
    args = parser.parse_args()
    app = create_app()
    db = DatabaseConnection(app.config["DATABASE"])
    try:
        count = (
            reconcile(db, app.config, datetime.now(TZ))
            if args.task == "reconcile"
            else reminders(db, app.config, datetime.now(TZ), send_email)
        )
        print(f"{args.task}: {count} registros processados.")
    except Exception:
        db.rollback()
        # Never print exception/request/response bodies, keys, PAN or CVV.
        print(
            "Rotina não concluída. Verifique a configuração e consulte os estados no painel.",
            file=sys.stderr,
        )
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
