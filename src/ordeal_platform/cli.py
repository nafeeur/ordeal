from __future__ import annotations

import argparse
import base64
import json
import logging
import os
from pathlib import Path
import sys
import time

from ordeal_agent.telemetry import PlatformClient

from . import __version__, db, schemas, services
from .config import Settings, env_value
from .operations import backup, restore, maintenance, deliver_one, system_identity
from .runner import CustomerRunner, RunnerPolicy, internal_work_once
from .security import issue_token


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, default=str))


def client_from_args(args):
    token = env_value(args.token_env)
    if not token:
        raise ValueError(f"Set {args.token_env} to a scoped service-account token")
    return PlatformClient(args.server, token, args.project)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ordeal-server", description="Standalone Ordeal platform and private runners")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Initialize the local database and create one organization")
    init.add_argument("--name", default="My organization")
    init.add_argument("--project-name", default="Default")
    serve = commands.add_parser("serve", help="Serve the API and web console")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--workers", type=int, default=1)
    commands.add_parser("migrate", help="Apply forward database migrations")
    create_org = commands.add_parser("create-org", help="Operator-only creation of an additional tenant")
    create_org.add_argument("name")
    token_cmd = commands.add_parser("issue-token", help="Operator recovery: issue a token for an existing principal")
    token_cmd.add_argument("principal_id")
    token_cmd.add_argument("--days", type=int, default=1)
    worker = commands.add_parser("worker", help="Run server-side evaluators; never executes uploaded Python")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--interval", type=float, default=2)
    scheduler = commands.add_parser("scheduler", help="Run retention, monitor checks and durable webhook deliveries")
    scheduler.add_argument("--once", action="store_true")
    scheduler.add_argument("--interval", type=float, default=30)
    grpc = commands.add_parser("otlp-grpc", help="Run the authenticated OTLP/gRPC listener")
    grpc.add_argument("--address", default="127.0.0.1:4317")
    grpc.add_argument("--cert")
    grpc.add_argument("--key")
    bak = commands.add_parser("backup", help="Create an authenticated encrypted local backup (SQLite + local artifacts)")
    bak.add_argument("output", type=Path)
    res = commands.add_parser("restore", help="Restore a verified local backup into an empty directory")
    res.add_argument("backup", type=Path)
    res.add_argument("destination", type=Path)
    runner = commands.add_parser("runner", help="Poll the control plane from a customer-controlled workspace")
    runner.add_argument("--server", default=os.environ.get("ORDEAL_SERVER_URL", "http://127.0.0.1:8080"))
    runner.add_argument("--project", default=os.environ.get("ORDEAL_PROJECT_ID", ""))
    runner.add_argument("--token-env", default="ORDEAL_API_KEY")
    runner.add_argument("--workspace", type=Path, default=Path.cwd())
    runner.add_argument("--allow-suite", action="append", required=True)
    runner.add_argument("--mode", required=True, choices=["docker", "trusted-process"])
    runner.add_argument("--image", default="")
    runner.add_argument("--timeout", type=float, default=300)
    runner.add_argument("--name", default="customer-runner")
    runner.add_argument("--label", action="append", default=[])
    runner.add_argument("--custom-evaluators", action="store_true")
    runner.add_argument("--once", action="store_true")
    runner.add_argument("--interval", type=float, default=2)
    for name in ("upload", "apply", "add-pack", "gate"):
        item = commands.add_parser(name)
        item.add_argument("file", type=Path)
        item.add_argument("--server", default=os.environ.get("ORDEAL_SERVER_URL", "http://127.0.0.1:8080"))
        item.add_argument("--project", default=os.environ.get("ORDEAL_PROJECT_ID", ""))
        item.add_argument("--token-env", default="ORDEAL_API_KEY")
        if name == "add-pack":
            item.add_argument("--sha256", required=True, help="Expected checksum of the local scenario pack")
        if name == "gate":
            item.add_argument("--baseline", type=Path)
            item.add_argument("--min-pass-rate", type=float, default=1.0)
            item.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if args.command == "init":
            root = Path(os.getenv("ORDEAL_DATA_DIR", ".ordeal"))
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            key_path = root / "master.key"
            if not env_value("ORDEAL_MASTER_KEY") and not key_path.exists():
                key = base64.urlsafe_b64encode(os.urandom(32)).decode()
                fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as handle:
                    handle.write(key + "\n")
            settings = Settings.from_env()
            settings.validate()
            database = db.Database(settings.database_url)
            database.migrate()
            with database.tx() as conn:
                existing = db.rows(conn, db.sa.select(db.tenants))
            if existing:
                emit({"initialized": True, "organizations": len(existing), "message": "Existing data preserved. Use issue-token for operator recovery or create-org for another tenant."})
            else:
                created = services.bootstrap(database, args.name, args.project_name)
                emit({**created, "url": settings.public_url, "warning": "Save the token securely. It expires in 30 days. Back up the master key separately."})
            return 0
        if args.command in {"runner", "upload", "apply", "add-pack", "gate"}:
            if args.command == "gate":
                from .ci import gate_report
                report = json.loads(args.file.read_text())
                baseline = json.loads(args.baseline.read_text()) if args.baseline else None
                outcome = gate_report(report, baseline=baseline, min_pass_rate=args.min_pass_rate)
                summary_path = args.summary or (Path(os.environ["GITHUB_STEP_SUMMARY"]) if os.getenv("GITHUB_STEP_SUMMARY") else None)
                if summary_path:
                    summary_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with summary_path.open("a") as handle:
                        handle.write(outcome["markdown"])
                emit(outcome)
                return 0 if outcome["passed"] else 1
            with client_from_args(args) as client:
                if args.command == "runner":
                    policy = RunnerPolicy(args.workspace, args.allow_suite, mode=args.mode, image=args.image, timeout=args.timeout)
                    runner = CustomerRunner(client, policy, name=args.name, labels=args.label, custom_evaluators=args.custom_evaluators)
                    while True:
                        try:
                            result = runner.run_once()
                            if result:
                                emit(result)
                        except Exception as exc:
                            logging.error("Runner iteration failed: %s", type(exc).__name__)
                            if args.once:
                                raise
                        if args.once:
                            break
                        time.sleep(max(.25, args.interval))
                elif args.command == "upload":
                    emit(client.upload_report(json.loads(args.file.read_text())))
                elif args.command == "apply":
                    from .provisioning import apply_manifest
                    emit(apply_manifest(client, json.loads(args.file.read_text())))
                elif args.command == "add-pack":
                    from .provisioning import install_pack
                    emit(install_pack(client, args.file, args.sha256))
            return 0
        if args.command == "restore":
            key = os.environ.get("ORDEAL_MASTER_KEY") or Settings.from_env().master_key
            emit(restore(args.backup, args.destination, key))
            return 0
        settings = Settings.from_env()
        settings.validate()
        database = db.Database(settings.database_url)
        if args.command == "migrate":
            database.migrate()
            emit({"schema_version": db.SCHEMA_VERSION})
        elif args.command == "serve":
            if args.workers > 1 and settings.database_url.startswith("sqlite"):
                raise ValueError("Use PostgreSQL for multi-worker serving; SQLite mode is for local/small deployments")
            import uvicorn
            uvicorn.run("ordeal_platform.server:create_app", factory=True, host=args.host, port=args.port,
                        workers=args.workers, proxy_headers=False, log_level="info")
        elif args.command == "create-org":
            database.check()
            emit(services.bootstrap(database, args.name))
        elif args.command == "issue-token":
            if not 1 <= args.days <= 365:
                raise ValueError("Token lifetime must be 1 to 365 days")
            with database.tx() as conn:
                principal = db.row(conn, db.sa.select(db.principals).where(db.principals.c.id == args.principal_id, db.principals.c.active == True))
                if not principal:
                    raise ValueError("Active principal not found")
                token, info = issue_token(conn, principal, "operator-recovery", args.days)
                db.audit_event(conn, principal["tenant_id"], "operator", "token.recovery_issued", info["id"])
                emit({"token": token, **info})
        elif args.command == "backup":
            emit(backup(settings, args.output))
        elif args.command in {"worker", "scheduler"}:
            database.check()
            runner_ids = {}
            while True:
                with database.tx() as conn:
                    tenants = db.rows(conn, db.sa.select(db.tenants))
                for tenant in tenants:
                    ident = system_identity(tenant["id"])
                    if args.command == "worker":
                        if tenant["id"] not in runner_ids:
                            with database.tx(tenant["id"]) as conn:
                                runner_ids[tenant["id"]] = services.register_runner(conn, ident, schemas.RunnerRegister(name="internal-evaluator", labels=["internal-evaluator"]))["id"]
                        result = internal_work_once(database, settings, ident, runner_ids[tenant["id"]])
                        if result:
                            emit(result)
                    else:
                        emit({"tenant_id": tenant["id"], **maintenance(database, settings, ident)})
                        for _ in range(100):
                            if not deliver_one(database, settings, ident):
                                break
                if args.once:
                    break
                time.sleep(max(.25, args.interval))
        elif args.command == "otlp-grpc":
            from .otlp import grpc_server
            import grpc
            credentials = None
            if args.cert and args.key:
                credentials = grpc.ssl_server_credentials([(Path(args.key).read_bytes(), Path(args.cert).read_bytes())])
            elif args.cert or args.key:
                raise ValueError("Both --cert and --key are required")
            server, port = grpc_server(database, settings, args.address, server_credentials=credentials)
            emit({"otlp_grpc_port": port, "tls": bool(credentials)})
            try:
                server.wait_for_termination()
            except KeyboardInterrupt:
                server.stop(5)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
