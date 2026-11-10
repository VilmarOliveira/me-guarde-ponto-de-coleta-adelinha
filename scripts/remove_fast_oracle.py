"""Remove the old provider's environment and exclusive units, without backup.
Run as the service operator after deploying this version. Does not call any API.
"""

import argparse, os, re, stat, subprocess, tempfile
from pathlib import Path


def remove_environment(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Use o arquivo real de ambiente, não um link simbólico.")
    content = path.read_text()
    info = path.stat()
    lines = content.splitlines(keepends=True)
    result = []
    public_origin = None
    has_site_origin = any(
        re.match(r"^\s*SITE_PUBLIC_ORIGIN\s*=", line) for line in lines
    )
    removed = 0
    continuation = False
    for line in lines:
        match = re.match(r"^\s*(?:export\s+)?FASTFLOW_[A-Z0-9_]+\s*=", line)
        if re.match(r"^\s*(?:export\s+)?FASTFLOW_PUBLIC_ORIGIN\s*=", line):
            public_origin = line.split("=", 1)[1].rstrip("\n")
        if match or continuation:
            removed += int(bool(match))
            continuation = line.rstrip().endswith("\\")
            continue
        result.append(line)
    if public_origin and not has_site_origin:
        if result and not result[-1].endswith("\n"):
            result[-1] += "\n"
        result.append("SITE_PUBLIC_ORIGIN=" + public_origin + "\n")
    descriptor, temp = tempfile.mkstemp(prefix=".environment-update-", dir=path.parent)
    try:
        os.fchmod(descriptor, stat.S_IMODE(info.st_mode))
        os.fchown(descriptor, info.st_uid, info.st_gid)
        with os.fdopen(descriptor, "w") as handle:
            handle.write("".join(result))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return removed


def main():
    parser = argparse.ArgumentParser(
        description="Remove variáveis/unidades exclusivas do provedor desativado, sem backup. Reiniciar aplica a exclusão de dados no banco."
    )
    parser.add_argument("--environment-file", default="/etc/ponto-coleta/site.env")
    parser.add_argument(
        "--service",
        default="ponto-coleta.service",
        choices=["ponto-coleta.service", "me-guarde.service"],
    )
    args = parser.parse_args()
    try:
        subprocess.run(["systemctl", "stop", args.service], check=True)
        for unit in [
            "ponto-coleta-plan-reconcile.timer",
            "ponto-coleta-plan-reconcile.service",
        ]:
            subprocess.run(
                ["systemctl", "disable", "--now", unit],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            Path("/etc/systemd/system", unit).unlink(missing_ok=True)
        count = remove_environment(args.environment_file)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "start", args.service], check=True)
        print(
            f"Removidas {count} variáveis da integração. Nenhum backup criado. Confira o estado do serviço e a migração de dados."
        )
    except Exception:
        print(
            "Remoção não concluída; confira o serviço e o arquivo de ambiente. Nenhuma cópia de segurança foi criada."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
