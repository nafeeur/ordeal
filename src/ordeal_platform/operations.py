from __future__ import annotations

import base64
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import secrets as random_secrets
import shutil
import smtplib
import sqlite3
import ssl
import tempfile
import time
import zipfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
import sqlalchemy as sa

from . import behavior, db, schemas, services
from .config import Settings
from .evaluators import evaluate
from .network import NetworkDenied, request as net_request
from .security import Identity, Vault, fail, redact
from .storage import ArtifactStore


def system_identity(tenant_id: str, actor="system") -> Identity:
    return Identity(actor, tenant_id, "admin", "service", "system", (), frozenset({"*"}))


def json_diff(before, after, path=""):
    if type(before) is not type(after):
        return [{"path": path or "/", "before": before, "after": after, "op": "replace"}]
    if isinstance(before, dict):
        changes = []
        for key in sorted(set(before) | set(after)):
            p = path + "/" + str(key).replace("~", "~0").replace("/", "~1")
            if key not in before:
                changes.append({"path": p, "op": "add", "after": after[key]})
            elif key not in after:
                changes.append({"path": p, "op": "remove", "before": before[key]})
            else:
                changes.extend(json_diff(before[key], after[key], p))
        return changes
    if isinstance(before, list):
        changes = []
        for i in range(max(len(before), len(after))):
            p = f"{path}/{i}"
            if i >= len(before):
                changes.append({"path": p, "op": "add", "after": after[i]})
            elif i >= len(after):
                changes.append({"path": p, "op": "remove", "before": before[i]})
            else:
                changes.extend(json_diff(before[i], after[i], p))
        return changes
    return [] if before == after else [{"path": path or "/", "op": "replace", "before": before, "after": after}]


def evaluate_trace(database, settings, ident, trace_id, evaluator_id, version_number):
    vault = Vault(settings.master_key)
    with database.tx(ident.tenant_id) as conn:
        trace = services.owned(conn, db.traces, ident, trace_id, "execute")
        evaluator = services.version(conn, ident, evaluator_id, version_number)
        if evaluator["kind"] != "evaluator" or evaluator["project_id"] != trace["project_id"]:
            fail(422, "Evaluator and trace must belong to the same project")
        spec = evaluator["data"]
        credentials = {}
        if spec.get("secret_name"):
            credentials[spec["secret_name"]] = services.get_secret(conn, ident, trace["project_id"], spec["secret_name"], vault, internal=True)
        cache_key = db.digest(["evaluator", evaluator["content_hash"], trace["content_hash"]])
        cached = None
        if spec.get("cache", False):
            cached = db.row(conn, db.scoped(db.cache, ident.tenant_id).where(db.cache.c.project_id == trace["project_id"],
                db.cache.c.key == cache_key, db.cache.c.expires_at > time.time()))
    # Remote requests happen outside the write transaction.
    result = cached["data"] if cached else evaluate(spec, trace["data"], settings=settings, get_secret=lambda name: credentials[name])
    result = redact(result)
    result = {**result, "trace_hash": trace["content_hash"], "evaluator_hash": evaluator["content_hash"], "cached": bool(cached)}
    item = dict(id=db.uid(), tenant_id=ident.tenant_id, project_id=trace["project_id"], trace_id=trace_id,
        evaluator_id=evaluator_id, evaluator_version=version_number, result=result, created_at=time.time())
    with database.tx(ident.tenant_id) as conn:
        # The trace may have been deleted while a remote judge was running.
        services.owned(conn, db.traces, ident, trace_id, "execute")
        conn.execute(db.evaluations.insert().values(**item))
        services.meter(conn, ident, trace["project_id"], "evaluations", 1)
        if not cached and spec.get("cache", False) and result["verdict"] in {"pass", "fail"}:
            old = db.row(conn, db.scoped(db.cache, ident.tenant_id).where(db.cache.c.project_id == trace["project_id"], db.cache.c.key == cache_key))
            data = {"data": result, "expires_at": time.time() + min(int(spec.get("cache_ttl_seconds", 3600)), 86400)}
            if old:
                conn.execute(db.cache.update().where(db.cache.c.id == old["id"]).values(**data))
            else:
                conn.execute(db.cache.insert().values(id=db.uid(), tenant_id=ident.tenant_id, project_id=trace["project_id"], key=cache_key, created_at=time.time(), **data))
        db.audit_event(conn, ident.tenant_id, ident.id, "evaluation.completed", item["id"],
                       {"verdict": result["verdict"], "trace_hash": trace["content_hash"], "evaluator_version": version_number})
        if result["verdict"] == "fail":
            services.emit_event(conn, ident, trace["project_id"], "evaluation.failed", {"trace_id": trace_id, "evaluation_id": item["id"], "verdict": "fail"})
    return item


def checkpoint(database, settings, ident):
    with database.tx(ident.tenant_id) as conn:
        db.audit_event(conn, ident.tenant_id, ident.id, "audit.checkpoint_exported")
        entries = db.rows(conn, db.scoped(db.audit, ident.tenant_id).order_by(db.audit.c.sequence))
    check = db.verify_audit(entries)
    payload = {"schema": "ordeal.audit-checkpoint/v1", "tenant_id": ident.tenant_id, "created_at": time.time(), **check}
    raw = base64.urlsafe_b64decode(settings.master_key)
    seed = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"ordeal-audit-signing-v1").derive(raw)
    private = Ed25519PrivateKey.from_private_bytes(seed)
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"checkpoint": payload, "algorithm": "Ed25519", "public_key": base64.b64encode(public).decode(),
            "signature": base64.b64encode(private.sign(db.canonical(payload).encode())).decode(),
            "note": "Retain a checkpoint outside Ordeal to detect a privileged rewrite. Pin the public key independently."}


def _monitor_value(spec, records, conn, ident, project_id):
    metric = spec["metric"]
    if not records:
        return None
    if metric == "evaluation_failure_rate":
        current = {r["id"]: r["content_hash"] for r in records}
        query = db.scoped(db.evaluations, ident.tenant_id).where(db.evaluations.c.project_id == project_id,
            db.evaluations.c.evaluator_id == spec["evaluator_id"],
            db.evaluations.c.evaluator_version == spec["evaluator_version"],
            db.evaluations.c.trace_id.in_(list(current)))
        latest = {}
        for row in conn.execute(query.order_by(db.evaluations.c.created_at.desc()).execution_options(yield_per=100)).mappings():
            if row["trace_id"] not in latest and row["result"].get("trace_hash") == current[row["trace_id"]]:
                latest[row["trace_id"]] = row["result"]
        scored = [x for x in latest.values() if x.get("verdict") in {"pass", "fail"}]
        if len(scored) < spec.get("minimum_samples", 20):
            return None
        return sum(x["verdict"] == "fail" for x in scored) / len(scored)
    if metric == "pass_rate":
        # Only executions with actual evaluative verdicts belong in this rate; unscored OTLP spans do not.
        scored = [r for r in records if r["status"] in {"pass", "fail", "error", "incomplete"}]
        return sum(r["status"] == "pass" for r in scored) / len(scored) if len(scored) >= spec.get("minimum_samples", 20) else None
    if metric == "error_rate":
        return sum(r["status"] == "error" for r in records) / len(records)
    if metric == "p95_latency_ms":
        return behavior.percentile([r["duration_ms"] for r in records], .95)
    if metric == "cost_usd":
        costs = [r["cost_usd"] for r in records if r["cost_usd"] is not None]
        return sum(costs) if costs else None
    if metric == "retry_rate":
        return sum("repeated_tool_calls" in behavior.classify(r["data"]) for r in records) / len(records)
    if metric == "behavior_drift":
        baseline_version = spec.get("baseline_version")
        if not baseline_version:
            return None
        baseline = db.rows(conn, db.scoped(db.traces, ident.tenant_id).where(db.traces.c.project_id == project_id,
            db.traces.c.agent_version == baseline_version).order_by(db.traces.c.created_at.desc()).limit(5000))
        candidates = [r for r in records if r["agent_version"] != baseline_version]
        return behavior.drift(baseline, candidates).get("jensen_shannon_divergence")
    return None


def maintenance(database, settings, ident):
    """One synchronous tenant maintenance iteration. Schedule externally or use CLI scheduler."""
    ident.require_org("identity")
    now = time.time()
    counters = {"traces_deleted": 0, "artifacts_deleted": 0, "monitors_evaluated": 0, "alerts_created": 0, "alerts_resolved": 0, "cache_deleted": 0}
    blobs_to_remove = []
    with database.tx(ident.tenant_id) as conn:
        projects = db.rows(conn, db.scoped(db.projects, ident.tenant_id))
        for proj in projects:
            services.lock_project(conn, proj["id"])
            cfg = proj["settings"]
            cutoff = now - cfg.get("retention_days", 30) * 86400
            expired = db.rows(conn, db.scoped(db.traces, ident.tenant_id).where(db.traces.c.project_id == proj["id"],
                db.traces.c.created_at < cutoff, db.traces.c.legal_hold == False).limit(1000))
            ids = [r["id"] for r in expired]
            if ids:
                for table in (db.evaluations, db.reviews):
                    conn.execute(table.delete().where(table.c.tenant_id == ident.tenant_id, table.c.trace_id.in_(ids)))
                conn.execute(db.traces.delete().where(db.traces.c.id.in_(ids), db.traces.c.tenant_id == ident.tenant_id))
                counters["traces_deleted"] += len(ids)
                db.audit_event(conn, ident.tenant_id, ident.id, "retention.traces_deleted", proj["id"], {"count": len(ids), "ids_hash": db.digest(ids)})
            artifact_cutoff = now - cfg.get("artifact_retention_days", 90) * 86400
            expired_artifacts = db.rows(conn, db.scoped(db.artifacts, ident.tenant_id).where(db.artifacts.c.project_id == proj["id"],
                db.artifacts.c.created_at < artifact_cutoff, db.artifacts.c.legal_hold == False).limit(1000))
            for artifact in expired_artifacts:
                conn.execute(db.artifacts.delete().where(db.artifacts.c.id == artifact["id"]))
                remaining = conn.execute(sa.select(sa.func.count()).select_from(db.artifacts).where(db.artifacts.c.tenant_id == ident.tenant_id,
                    db.artifacts.c.path == artifact["path"])).scalar()
                if not remaining:
                    blobs_to_remove.append(artifact["path"])
                counters["artifacts_deleted"] += 1
                db.audit_event(conn, ident.tenant_id, ident.id, "retention.artifact_deleted", artifact["id"])
            monitors = db.rows(conn, db.scoped(db.resources, ident.tenant_id).where(db.resources.c.project_id == proj["id"],
                db.resources.c.kind == "monitor", db.resources.c.deleted == False))
            for monitor in monitors:
                ver = services.version(conn, ident, monitor["id"])
                spec = ver["data"]
                if not spec.get("enabled", True):
                    continue
                query = db.scoped(db.traces, ident.tenant_id).where(db.traces.c.project_id == proj["id"],
                    db.traces.c.created_at >= now - spec.get("window_seconds", 3600))
                if spec.get("environment"):
                    query = query.where(db.traces.c.environment == spec["environment"])
                records = db.rows(conn, query.order_by(db.traces.c.created_at.desc()).limit(10000))
                if len(records) < spec.get("minimum_samples", 20):
                    continue
                value = _monitor_value(spec, records, conn, ident, proj["id"])
                if value is None:
                    continue
                counters["monitors_evaluated"] += 1
                fires = value > spec["threshold"] if spec.get("operator", "gt") == "gt" else value < spec["threshold"]
                active = db.row(conn, db.scoped(db.alerts, ident.tenant_id).where(db.alerts.c.monitor_id == monitor["id"],
                    db.alerts.c.state.in_(["firing", "acknowledged"])).order_by(db.alerts.c.created_at.desc()).limit(1))
                if fires and not active:
                    data = {"monitor": monitor["name"], "monitor_version": ver["version"], "metric": spec["metric"], "value": value,
                            "threshold": spec["threshold"], "samples": len(records), "window_seconds": spec.get("window_seconds", 3600)}
                    alert = dict(id=db.uid(), tenant_id=ident.tenant_id, project_id=proj["id"], monitor_id=monitor["id"],
                                 state="firing", data=data, created_at=now)
                    conn.execute(db.alerts.insert().values(**alert))
                    counters["alerts_created"] += 1
                    services.emit_event(conn, ident, proj["id"], "monitor.triggered", {"alert_id": alert["id"], **data})
                    db.audit_event(conn, ident.tenant_id, ident.id, "monitor.triggered", monitor["id"], data)
                elif not fires and active:
                    conn.execute(db.alerts.update().where(db.alerts.c.id == active["id"]).values(state="resolved"))
                    counters["alerts_resolved"] += 1
                    services.emit_event(conn, ident, proj["id"], "monitor.resolved", {"alert_id": active["id"], "monitor_id": monitor["id"]})
        expired_cache = conn.execute(db.cache.delete().where(db.cache.c.tenant_id == ident.tenant_id, db.cache.c.expires_at <= now))
        counters["cache_deleted"] = expired_cache.rowcount
        conn.execute(db.tokens.delete().where(db.tokens.c.tenant_id == ident.tenant_id, db.tokens.c.expires_at < now - 7 * 86400))
    store = ArtifactStore(settings)
    # Recheck reachability under a transaction so a concurrently uploaded identical blob is not removed.
    for key in blobs_to_remove:
        with database.tx(ident.tenant_id) as conn:
            remaining = conn.execute(sa.select(sa.func.count()).select_from(db.artifacts).where(db.artifacts.c.tenant_id == ident.tenant_id,
                db.artifacts.c.path == key)).scalar()
            if not remaining:
                store.delete(key)
    return counters


def connector_request(config, envelope, secret=""):
    """Build provider-specific payloads; all are opt-in and delivered by the restricted HTTP client."""
    kind = config.get("type", "webhook")
    data = envelope["data"]
    title = config.get("title", "Ordeal: " + envelope["type"])
    text = title + "\n" + json.dumps(data, ensure_ascii=False, sort_keys=True)
    url = config.get("url", "")
    headers = {"Content-Type": "application/json", "User-Agent": "Ordeal/0.3", "X-Ordeal-Event-Id": envelope["id"]}
    if kind == "slack":
        body = {"text": text}
    elif kind == "teams":
        body = {"@type": "MessageCard", "@context": "https://schema.org/extensions", "summary": title, "text": text}
    elif kind == "pagerduty":
        body = {"routing_key": secret, "event_action": "resolve" if envelope["type"].endswith("resolved") else "trigger",
                "dedup_key": data.get("alert_id", envelope["id"]),
                "payload": {"summary": title, "source": "ordeal", "severity": "error", "custom_details": data}}
    elif kind in {"github_status", "gitlab_status"}:
        sha = str(data.get("git_sha") or config.get("git_sha", ""))
        import re
        if not re.fullmatch(r"[a-fA-F0-9]{40}", sha):
            raise ValueError("GitHub status connector requires an exact 40-character commit SHA")
        url = url.replace("{sha}", sha)
        # Only explicit success can produce a green status. Unknown is pending, not PASS.
        status = "failure" if data.get("passed") is False or data.get("verdict") in {"fail", "error", "incomplete"} else (
            "success" if data.get("passed") is True or data.get("verdict") == "pass" else "pending")
        body = {"state": status,
                "context": "ordeal/agent-behavior", "description": title[:140]}
        if config.get("target_url"):
            body["target_url"] = config["target_url"]
        if kind == "gitlab_status":
            body["name"] = body.pop("context")
            if body["state"] == "failure": body["state"] = "failed"
            headers["PRIVATE-TOKEN"] = secret
        else:
            headers.update({"Authorization": "Bearer " + secret, "Accept": "application/vnd.github+json"})
    elif kind == "github_issue":
        body = {"title": title[:256], "body": text, "labels": config.get("labels", [])}
        headers.update({"Authorization": "Bearer " + secret, "Accept": "application/vnd.github+json"})
    elif kind == "gitlab_issue":
        body = {"title": title[:256], "description": text}
        headers["PRIVATE-TOKEN"] = secret
    elif kind == "jira":
        body = {"fields": {"project": {"key": config["project_key"]}, "summary": title[:255],
                           "description": text, "issuetype": {"name": config.get("issue_type", "Bug")}}}
        headers["Authorization"] = config.get("auth_scheme", "Basic") + " " + secret
    elif kind == "linear":
        body = {"query": "mutation OrdealIssue($input: IssueCreateInput!) { issueCreate(input: $input) { success issue { id identifier } } }",
                "variables": {"input": {"teamId": config["team_id"], "title": title[:255], "description": text}}}
        headers["Authorization"] = secret
    elif kind == "servicenow":
        body = {"short_description": title[:160], "description": text}
        headers["Authorization"] = config.get("auth_scheme", "Bearer") + " " + secret
    else:
        body = envelope
    encoded = db.canonical(body).encode()
    timestamp = str(int(time.time()))
    if secret and kind == "webhook":
        headers["X-Ordeal-Timestamp"] = timestamp
        headers["X-Ordeal-Signature"] = "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + encoded, hashlib.sha256).hexdigest()
    return url, headers, encoded


def send_smtp(config, envelope):
    host = os.environ.get("ORDEAL_SMTP_HOST")
    sender = os.environ.get("ORDEAL_SMTP_FROM")
    if not host or not sender:
        raise ValueError("SMTP relay not configured by operator")
    message = EmailMessage()
    message["From"], message["To"] = sender, ", ".join(config["recipients"])
    message["Subject"] = "Ordeal: " + envelope["type"]
    message.set_content(json.dumps(envelope["data"], indent=2, ensure_ascii=False))
    port = int(os.environ.get("ORDEAL_SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context()) as smtp:
        if os.environ.get("ORDEAL_SMTP_USER"):
            smtp.login(os.environ["ORDEAL_SMTP_USER"], os.environ["ORDEAL_SMTP_PASSWORD"])
        smtp.send_message(message)


def deliver_one(database, settings, ident) -> dict | None:
    ident.require_org("secret")
    now = time.time()
    with database.tx(ident.tenant_id) as conn:
        item = db.row(conn, db.scoped(db.deliveries, ident.tenant_id).where(sa.or_(
            sa.and_(db.deliveries.c.state == "pending", db.deliveries.c.not_before <= now),
            sa.and_(db.deliveries.c.state == "delivering", db.deliveries.c.lease_until < now))).order_by(db.deliveries.c.created_at).limit(1).with_for_update(skip_locked=True))
        if not item:
            return None
        token = random_secrets.token_urlsafe(32)
        attempt = item["attempt"] + 1
        conn.execute(db.deliveries.update().where(db.deliveries.c.id == item["id"]).values(state="delivering", attempt=attempt, lease_token=token, lease_until=now + 60))
        try:
            config = services.version(conn, ident, item["connector_id"], item["connector_version"])["data"]
            secret = services.get_secret(conn, ident, item["project_id"], config["secret_name"], Vault(settings.master_key), internal=True) if config.get("secret_name") else ""
        except Exception as exc:
            # A deleted connector/credential is a configuration failure, not an endlessly reclaimable job.
            error = "Configuration unavailable: " + type(exc).__name__
            conn.execute(db.deliveries.update().where(db.deliveries.c.id == item["id"]).values(
                state="dead", lease_token=None, lease_until=None, last_error=error))
            db.audit_event(conn, ident.tenant_id, ident.id, "delivery.dead", item["id"], {"error_type": error})
            return {"id": item["id"], "state": "dead", "attempt": attempt, "error": error}
    permanent = False
    error = None
    try:
        if config.get("type") == "smtp":
            send_smtp(config, item["payload"])
            code = 200
        else:
            url, headers, body = connector_request(config, item["payload"], secret)
            code, _, reply = net_request(settings, "POST", url, body=body, headers=headers)
            if config.get("type") == "linear" and 200 <= code < 300:
                decoded = json.loads(reply)
                if decoded.get("errors") or not decoded.get("data", {}).get("issueCreate", {}).get("success"):
                    raise ValueError("Linear rejected the GraphQL mutation")
        if not 200 <= code < 300:
            error = f"HTTP {code}"
            permanent = 400 <= code < 500 and code not in {408, 429}
    except Exception as exc:
        error = type(exc).__name__
        permanent = isinstance(exc, (NetworkDenied, ValueError, KeyError))
    max_attempts = min(int(config.get("max_attempts", 5)), 10)
    state = "delivered" if error is None else ("dead" if permanent or attempt >= max_attempts else "pending")
    with database.tx(ident.tenant_id) as conn:
        updated = conn.execute(db.deliveries.update().where(db.deliveries.c.id == item["id"], db.deliveries.c.lease_token == token,
            db.deliveries.c.state == "delivering").values(state=state, last_error=error, lease_token=None, lease_until=None,
            not_before=time.time() + min(3600, 2 ** attempt * 5)))
        if updated.rowcount:
            db.audit_event(conn, ident.tenant_id, ident.id, "delivery." + state, item["id"], {"attempt": attempt, "error_type": error})
    return {"id": item["id"], "state": state, "attempt": attempt, "error": error}


def backup(settings: Settings, output: Path) -> dict:
    if not settings.database_url.startswith("sqlite:///") or ":memory:" in settings.database_url:
        raise ValueError("Bundled backup command is for SQLite. PostgreSQL requires the documented pg_dump/PITR and object-store snapshot procedure.")
    if settings.s3_bucket:
        raise ValueError("Local backup excludes S3; use coordinated database and bucket snapshots as documented")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    database = db.Database(settings.database_url)
    source_path = settings.database_url[len("sqlite:///"):]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target_db = root / "database.sqlite"
        manifest = {"schema": "ordeal.backup/v1", "schema_version": db.SCHEMA_VERSION, "created_at": time.time(), "files": {}}
        # Lock writers while capturing DB + referenced immutable artifact bytes.
        with database.tx() as conn:
            with sqlite3.connect(source_path) as source, sqlite3.connect(target_db) as destination:
                source.backup(destination)
            files = {"database.sqlite": target_db.read_bytes()}
            for item in db.rows(conn, sa.select(db.artifacts)):
                path = settings.data_dir / "artifacts" / item["path"]
                files["artifacts/" + item["path"]] = path.read_bytes()
            total = sum(len(blob) for blob in files.values())
            if total > 512 * 1024 * 1024:
                raise ValueError("Local backup size exceeds 512 MiB; use the large-deployment backup procedure")
        for name, blob in files.items():
            manifest["files"][name] = hashlib.sha256(blob).hexdigest()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
            for name, blob in files.items():
                archive.writestr(name, blob)
        encrypted = b"ORDEAL-BACKUP-1\n" + Vault(settings.master_key).encrypt_bytes(buffer.getvalue(), "ordeal-backup/v1")
        fd, temporary = tempfile.mkstemp(dir=output.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    database.engine.dispose()
    return {"path": str(output), "sha256": hashlib.sha256(encrypted).hexdigest(), "files": len(manifest["files"]), "encrypted": True}


def restore(backup_path: Path, destination: Path, master_key: str) -> dict:
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Restore destination must be empty")
    blob = Path(backup_path).read_bytes()
    prefix = b"ORDEAL-BACKUP-1\n"
    if not blob.startswith(prefix):
        raise ValueError("Unsupported backup format")
    plain = Vault(master_key).decrypt_bytes(blob[len(prefix):], "ordeal-backup/v1")
    with zipfile.ZipFile(io.BytesIO(plain)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("Duplicate backup members")
        if sum(x.file_size for x in archive.infolist()) > 512 * 1024 * 1024:
            raise ValueError("Backup exceeds restore size limit")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("schema") != "ordeal.backup/v1" or set(names) != set(manifest["files"]) | {"manifest.json"}:
            raise ValueError("Invalid backup manifest")
        if manifest.get("schema_version") != db.SCHEMA_VERSION:
            raise ValueError("Backup schema does not match this release")
        import re
        for name, checksum in manifest["files"].items():
            if name != "database.sqlite" and not re.fullmatch(r"artifacts/[a-f0-9]{32}/[a-f0-9]{64}", name):
                raise ValueError("Unsafe backup path")
            if hashlib.sha256(archive.read(name)).hexdigest() != checksum:
                raise ValueError("Backup checksum mismatch")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(dir=destination.parent))
        try:
            for name in manifest["files"]:
                target = temporary / ("platform.db" if name == "database.sqlite" else name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
                target.chmod(0o600)
            (temporary / "master.key").write_text(master_key + "\n")
            (temporary / "master.key").chmod(0o600)
            with sqlite3.connect(temporary / "platform.db") as con:
                if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Restored database failed integrity check")
            if destination.exists():
                destination.rmdir()
            os.replace(temporary, destination)
        except BaseException:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    return {"restored": str(destination), "files": len(manifest["files"]), "integrity": "ok"}
