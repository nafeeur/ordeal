from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
import secrets as random_secrets
import time
from typing import Any

import jsonschema
import sqlalchemy as sa

from . import db, schemas
from .config import Settings
from .evaluators import validate_spec
from .network import validate_url
from .security import Identity, ROLE_PERMISSIONS, PRIVILEGED_KINDS, Vault, fail, issue_token, redact


def bootstrap(database: db.Database, name: str, project_name="Default") -> dict:
    tenant_id, principal_id, project_id = db.uid(), db.uid(), db.uid()
    with database.tx() as conn:
        conn.execute(db.tenants.insert().values(id=tenant_id, name=name, created_at=time.time()))
        p = dict(id=principal_id, tenant_id=tenant_id, name="Owner", username=None, external_id=None,
                 role="owner", kind="human", projects=[], permissions=[], active=True, created_at=time.time())
        conn.execute(db.principals.insert().values(**p))
        token, _ = issue_token(conn, p, "bootstrap", 30)
        conn.execute(db.projects.insert().values(id=project_id, tenant_id=tenant_id, name=project_name,
                     settings=schemas.ProjectSettings().model_dump(), created_at=time.time()))
        db.audit_event(conn, tenant_id, principal_id, "organization.created", tenant_id)
    return {"tenant_id": tenant_id, "project_id": project_id, "principal_id": principal_id, "token": token}


def project(conn, ident: Identity, project_id: str, permission="read") -> dict:
    ident.require(permission, project_id)
    item = db.row(conn, db.scoped(db.projects, ident.tenant_id).where(db.projects.c.id == project_id))
    if not item:
        fail(404, "Project not found")
    return item


def owned(conn, table, ident, item_id: str, permission="read") -> dict:
    item = db.row(conn, db.scoped(table, ident.tenant_id).where(table.c.id == item_id))
    if not item:
        fail(404, "Resource not found")
    if "project_id" in item:
        project(conn, ident, item["project_id"], permission)
    else:
        ident.require(permission)
    return item


def lock_project(conn, project_id: str):
    conn.execute(sa.select(db.projects.c.id).where(db.projects.c.id == project_id).with_for_update())


def version(conn, ident, resource_id, number=None, permission="read") -> dict:
    obj = owned(conn, db.resources, ident, resource_id, permission)
    if obj["deleted"]:
        fail(404, "Resource deleted")
    number = number or obj["head"]
    ver = db.row(conn, db.scoped(db.versions, ident.tenant_id).where(
        db.versions.c.resource_id == resource_id, db.versions.c.version == number))
    if not ver:
        fail(404, "Resource version not found")
    return {**obj, "version": ver["version"], "data": ver["data"], "content_hash": ver["content_hash"], "author": ver["author"]}


def validate_resource(conn, ident, project_id: str, kind: str, data: dict, settings: Settings):
    ident.require(PRIVILEGED_KINDS.get(kind, "write"), project_id)
    # Guard configuration surfaces capable of routing secrets or changing privileged operations.
    if data.get("secret_name") or data.get("secret_refs"):
        ident.require("secret", project_id)
    if kind == "dataset":
        cases = data.get("cases", [])
        if not isinstance(cases, list) or len(cases) > 50000:
            fail(422, "Dataset cases must be a list of at most 50,000 records")
        ids = [c.get("id") if isinstance(c, dict) else None for c in cases]
        if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            fail(422, "Dataset case ids must be nonempty and unique")
        if data.get("schema"):
            jsonschema.Draft202012Validator.check_schema(data["schema"])
            for case in cases:
                jsonschema.validate(case, data["schema"])
    elif kind == "evaluator":
        validate_spec(data)
        if data.get("endpoint"):
            validate_url(data["endpoint"], settings)
    elif kind == "prompt":
        if not isinstance(data.get("template"), str):
            fail(422, "Prompt requires a template string")
        from .prompts import fields
        needed = fields(data["template"])
        if "variables" in data and (not isinstance(data["variables"], list) or any(not isinstance(v, str) for v in data["variables"]) or set(data["variables"]) != needed):
            fail(422, "Prompt variables must list exactly the template field names")
    elif kind == "connector":
        if data.get("type", "webhook") not in {"webhook", "slack", "teams", "pagerduty", "github_status", "github_issue", "gitlab_status", "gitlab_issue", "jira", "linear", "servicenow", "smtp"}:
            fail(422, "Unsupported connector type")
        if data.get("type") != "smtp":
            validate_url(data.get("url", ""), settings)
        else:
            # SMTP is configured exclusively by operator environment, never arbitrary relay URLs.
            if not isinstance(data.get("recipients"), list) or not data["recipients"]:
                fail(422, "SMTP connectors require recipients and an operator-configured relay")
        if not isinstance(data.get("events", ["*"]), list):
            fail(422, "Connector events must be a list")
    elif kind == "monitor":
        if data.get("metric") not in {"pass_rate", "error_rate", "p95_latency_ms", "cost_usd", "retry_rate", "behavior_drift", "evaluation_failure_rate"}:
            fail(422, "Unsupported monitor metric")
        if data.get("operator", "gt") not in {"gt", "lt"} or not isinstance(data.get("threshold"), (int, float)) or not math.isfinite(data["threshold"]):
            fail(422, "Monitor requires finite threshold and operator gt/lt")
        if type(data.get("minimum_samples", 20)) is not int or not 1 <= data.get("minimum_samples", 20) <= 10000:
            fail(422, "minimum_samples must be an integer between 1 and 10000")
        if data["metric"] == "evaluation_failure_rate":
            if type(data.get("evaluator_version")) is not int or data["evaluator_version"] < 1 or not isinstance(data.get("evaluator_id"), str):
                fail(422, "Evaluation monitors must pin evaluator_id and evaluator_version")
            evaluator = version(conn, ident, data["evaluator_id"], data["evaluator_version"])
            if evaluator["kind"] != "evaluator" or evaluator["project_id"] != project_id:
                fail(422, "Evaluation monitor must reference an evaluator in this project")
        if type(data.get("window_seconds", 3600)) is not int or not 60 <= data.get("window_seconds", 3600) <= 2592000:
            fail(422, "Monitor window must be between 60 seconds and 30 days")
    elif kind == "automation":
        if data.get("event") not in {"dataset.updated", "prompt.updated", "agent.updated", "experiment.finished"}:
            fail(422, "Unsupported automation event")
        schemas.JobCreate.model_validate(data.get("job", {}))
    elif kind == "experiment":
        if not isinstance(data.get("report"), dict) or not isinstance(data["report"].get("results"), list):
            fail(422, "Experiment requires a SuiteReport under report")
        references = data.get("references", [])
        if not isinstance(references, list) or any(not isinstance(r, dict) or not isinstance(r.get("id"), str) or type(r.get("version")) is not int or r["version"] < 1 for r in references):
            fail(422, "Experiment references require ids and positive immutable versions")
        for reference in references:
            obj = version(conn, ident, reference["id"], reference["version"])
            if obj["project_id"] != project_id:
                fail(422, "Experiment references must belong to this project")
    elif kind == "bundle":
        if not isinstance(data.get("scenarios", []), list):
            fail(422, "Bundle scenarios must be a list")
    db.canonical(data)  # Reject non-finite numbers anywhere in nested data.


def create_resource(conn, ident, project_id, body: schemas.ResourceCreate, settings, *, emit_finished=True) -> dict:
    project(conn, ident, project_id, PRIVILEGED_KINDS.get(body.kind, "write"))
    validate_resource(conn, ident, project_id, body.kind, body.data, settings)
    lock_project(conn, project_id)
    existing = db.row(conn, db.scoped(db.resources, ident.tenant_id).where(
        db.resources.c.project_id == project_id, db.resources.c.kind == body.kind, db.resources.c.name == body.name))
    if existing:
        fail(409, "Resource name already exists; create a version instead")
    obj = dict(id=db.uid(), tenant_id=ident.tenant_id, project_id=project_id, kind=body.kind,
               name=body.name, head=1, aliases={}, deleted=False, created_at=time.time())
    conn.execute(db.resources.insert().values(**obj))
    conn.execute(db.versions.insert().values(id=db.uid(), tenant_id=ident.tenant_id, resource_id=obj["id"],
        version=1, data=body.data, content_hash=db.digest(body.data), author=ident.id, created_at=time.time()))
    db.audit_event(conn, ident.tenant_id, ident.id, f"{body.kind}.created", obj["id"], {"version": 1})
    emit_event(conn, ident, project_id, f"{body.kind}.created", {"resource_id": obj["id"], "version": 1})
    if body.kind == "experiment" and emit_finished:
        report = body.data["report"]
        from .ci import gate_report
        emit_event(conn, ident, project_id, "experiment.finished", {"experiment_id": obj["id"], "version": 1,
            "passed": gate_report(report)["passed"], "git_sha": report.get("git_sha"), "automation_depth": 0})
    return version(conn, ident, obj["id"])


def add_version(conn, ident, resource_id, body: schemas.VersionCreate, settings) -> dict:
    obj = owned(conn, db.resources, ident, resource_id, "read")
    if obj["deleted"]:
        fail(404, "Resource deleted")
    validate_resource(conn, ident, obj["project_id"], obj["kind"], body.data, settings)
    lock_project(conn, obj["project_id"])
    obj = owned(conn, db.resources, ident, resource_id, "read")
    if obj["head"] != body.base_version:
        fail(409, "Stale base_version; reload the latest resource version")
    new_number = obj["head"] + 1
    changed = conn.execute(db.resources.update().where(db.resources.c.id == resource_id,
        db.resources.c.tenant_id == ident.tenant_id, db.resources.c.head == body.base_version).values(head=new_number))
    if changed.rowcount != 1:
        fail(409, "Concurrent resource update")
    conn.execute(db.versions.insert().values(id=db.uid(), tenant_id=ident.tenant_id, resource_id=resource_id,
        version=new_number, data=body.data, content_hash=db.digest(body.data), author=ident.id, created_at=time.time()))
    db.audit_event(conn, ident.tenant_id, ident.id, f"{obj['kind']}.updated", resource_id, {"version": new_number})
    emit_event(conn, ident, obj["project_id"], f"{obj['kind']}.updated", {"resource_id": resource_id, "version": new_number})
    return version(conn, ident, resource_id)


def meter(conn, ident, project_id, metric, amount=1, *, period=None, limit=None):
    lock_project(conn, project_id)
    period = period or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    query = db.scoped(db.usage, ident.tenant_id).where(db.usage.c.project_id == project_id,
                                                   db.usage.c.period == period, db.usage.c.metric == metric)
    item = db.row(conn, query)
    value = (item["value"] if item else 0) + amount
    if limit is not None and value > limit:
        fail(429, f"Project quota exceeded: {metric}")
    if item:
        conn.execute(db.usage.update().where(db.usage.c.id == item["id"]).values(value=value))
    else:
        conn.execute(db.usage.insert().values(id=db.uid(), tenant_id=ident.tenant_id, project_id=project_id,
            period=period, metric=metric, value=value, created_at=time.time()))
    return value


def emit_event(conn, ident, project_id, event, payload):
    event_id = db.uid()
    configs = db.rows(conn, db.scoped(db.resources, ident.tenant_id).where(db.resources.c.project_id == project_id,
        db.resources.c.kind.in_(["connector", "automation"]), db.resources.c.deleted == False))
    for cfg in configs:
        internal = Identity(ident.id, ident.tenant_id, "admin", "service", "event-router", (), frozenset({"*"}))
        ver = version(conn, internal, cfg["id"])
        data = ver["data"]
        if not data.get("enabled", True):
            continue
        if cfg["kind"] == "connector" and (event in data.get("events", ["*"]) or "*" in data.get("events", ["*"])):
            conn.execute(db.deliveries.insert().values(id=db.uid(), tenant_id=ident.tenant_id, project_id=project_id,
                connector_id=cfg["id"], connector_version=ver["version"], event_id=event_id, event=event,
                payload={"id": event_id, "type": event, "created_at": time.time(), "project_id": project_id, "data": payload},
                state="pending", attempt=0, not_before=time.time(), lease_token=None, lease_until=None, last_error=None,
                created_at=time.time()))
        elif cfg["kind"] == "automation" and data.get("event") == event:
            # One automation generation per completion prevents endless experiment->job->experiment loops.
            if payload.get("automation_depth", 0):
                continue
            spec = schemas.JobCreate.model_validate(data["job"])
            spec.idempotency_key = f"auto:{event_id}:{cfg['id']}"
            spec.payload = {**spec.payload, "_automation_depth": 1}
            # Configuration is administrator-only; evaluated under that delegated platform permission.
            system = Identity(ident.id, ident.tenant_id, "admin", "service", "automation", (), frozenset({"*"}))
            enqueue(conn, system, project_id, spec)


def redact_for_project(data, proj):
    cfg = proj["settings"]
    return redact(data, suppress_inputs=cfg.get("suppress_inputs", False), suppress_outputs=cfg.get("suppress_outputs", False),
        sensitive_fields=cfg.get("sensitive_fields", []), pii=cfg.get("redact_pii", True))


def ingest_trace(conn, ident, project_id, trace: schemas.TraceIn, settings) -> tuple[dict, bool]:
    proj = project(conn, ident, project_id, "write")
    lock_project(conn, project_id)
    data = trace.model_dump()
    data["trace_id"] = trace.trace_id or db.uid()
    events = data["trajectory"].get("events", [])
    if not isinstance(events, list) or len(events) > settings.max_trace_events:
        fail(422, "Invalid or excessive trace events")
    if any(not isinstance(e, dict) or not isinstance(e.get("kind"), str) or not isinstance(e.get("name"), str) for e in events):
        fail(422, "Events require string kind and name")
    for field in ("cost_usd", "total_tokens", "input_tokens", "output_tokens"):
        value = data["usage"].get(field)
        if value is not None and (type(value) not in {int, float} or not math.isfinite(value) or value < 0):
            fail(422, "Usage fields must be finite nonnegative numbers")
    data = redact_for_project(data, proj)
    checksum = db.digest(data)
    existing = db.row(conn, db.scoped(db.traces, ident.tenant_id).where(
        db.traces.c.project_id == project_id, db.traces.c.trace_id == data["trace_id"]))
    if existing:
        if existing["content_hash"] != checksum:
            fail(409, "Trace id already exists with different content")
        return existing, False
    limit = proj["settings"].get("limits", {})
    meter(conn, ident, project_id, "traces", limit=limit.get("traces_daily"))
    meter(conn, ident, project_id, "trace_bytes", len(db.canonical(data).encode()), limit=limit.get("bytes_daily"))
    item = dict(id=db.uid(), tenant_id=ident.tenant_id, project_id=project_id, trace_id=data["trace_id"],
        name=data["name"], environment=data["environment"], agent_version=data.get("agent_version"), status=data["status"],
        duration_ms=data["duration_ms"], cost_usd=data["usage"].get("cost_usd"), total_tokens=data["usage"].get("total_tokens"),
        data=data, content_hash=checksum, legal_hold=False, created_at=time.time())
    conn.execute(db.traces.insert().values(**item))
    db.audit_event(conn, ident.tenant_id, ident.id, "trace.ingested", item["id"], {"hash": checksum})
    if item["status"] in {"fail", "error"}:
        emit_event(conn, ident, project_id, "trace.failed", {"trace_id": item["id"], "status": item["status"]})
    for evaluator in proj["settings"].get("online_evaluators", []):
        bucket = int(hashlib.sha256((item["trace_id"] + evaluator["id"]).encode()).hexdigest()[:16], 16) / 2 ** 64
        if bucket < evaluator.get("sample_rate", 1):
            system = Identity(ident.id, ident.tenant_id, "admin", "service", "online-eval", (), frozenset({"*"}))
            target = version(conn, system, evaluator["id"], evaluator["version"])
            if target["project_id"] != project_id or target["kind"] != "evaluator":
                fail(422, "Invalid online evaluator reference")
            system = Identity(ident.id, ident.tenant_id, "admin", "service", "online-eval", (), frozenset({"*"}))
            enqueue(conn, system, project_id, schemas.JobCreate(kind="evaluate", payload={"trace_id": item["id"],
                "evaluator_id": evaluator["id"], "version": evaluator["version"]}, idempotency_key="online:" + item["id"] + ":" + evaluator["id"]))
    return item, True


def _redacted(value):
    if isinstance(value, dict): return any(_redacted(v) for v in value.values())
    if isinstance(value, list): return any(_redacted(v) for v in value)
    return isinstance(value, str) and any(marker in value for marker in ("[REDACTED]", "[EMAIL]", "[SECRET]", "[SSN]", "[DEPTH_LIMIT]"))


def trace_case(trace: dict, instruction: str, expected: Any = None) -> dict:
    data = trace["data"]
    pending = []
    calls = []
    issues = []
    events = data.get("trajectory", {}).get("events", [])
    call_order = {}
    for ordinal, event in enumerate(events):
        if event.get("kind") == "tool_call":
            call_order[id(event)] = ordinal
            pending.append(event)
        elif event.get("kind") == "tool_result":
            payload = event.get("payload", {})
            # Prefer invocation identifiers, then tool + ordinal. Do not silently pair mismatched spans.
            candidates = [e for e in pending if e["name"] == event.get("name") and (
                payload.get("call_id") is None or e.get("payload", {}).get("call_id") == payload["call_id"]) ]
            if not candidates:
                issues.append("Unmatched tool result")
                continue
            call = candidates[0]
            pending.remove(call)
            args = call.get("payload", {}).get("arguments")
            if not isinstance(args, dict) or "result" not in payload or _redacted(args) or _redacted(payload["result"]):
                issues.append("Tool inputs or outputs unavailable")
                continue
            calls.append({"tool": call["name"], "arguments": args, "result": payload["result"], "error": None, "_ordinal": call_order[id(call)]})
    calls.sort(key=lambda call: call["_ordinal"])
    for call in calls: call.pop("_ordinal")
    if pending:
        issues.append("One or more calls have no captured result")
    if not calls:
        issues.append("No replayable tool calls")
    return {"id": trace["id"], "instruction": instruction, "expected": expected,
            "source": {"trace_id": trace["id"], "trace_hash": trace["content_hash"]},
            "cassette": {"schema": "ordeal.cassette/v1", "name": trace["name"],
                         "initial_state": data.get("trajectory", {}).get("initial_state", {}), "calls": calls},
            "replay_ready": not issues, "capture_gaps": issues}


def enqueue(conn, ident, project_id, spec: schemas.JobCreate) -> dict:
    proj = project(conn, ident, project_id, "execute")
    lock_project(conn, project_id)
    if spec.kind == "evaluate":
        selected = version(conn, ident, spec.payload.get("evaluator_id", ""), spec.payload.get("version"))
        if selected["data"].get("type") == "python" and "custom-evaluator" not in spec.labels:
            spec.labels = [*spec.labels, "custom-evaluator"]
    if spec.idempotency_key:
        old = db.row(conn, db.scoped(db.jobs, ident.tenant_id).where(db.jobs.c.project_id == project_id,
            db.jobs.c.idempotency_key == spec.idempotency_key))
        if old:
            if old["kind"] != spec.kind or old["payload"] != spec.payload or old["labels"] != spec.labels:
                fail(409, "Idempotency key was reused for a different job")
            return old
    if spec.payload.get("secret_refs"):
        ident.require("secret", project_id)
    if spec.kind == "suite":
        suite_path = spec.payload.get("suite_path")
        from pathlib import PurePosixPath
        if not isinstance(suite_path, str) or not suite_path.endswith(".py") or PurePosixPath(suite_path).is_absolute() or ".." in PurePosixPath(suite_path).parts or "\\" in suite_path:
            fail(422, "Suite jobs require a relative .py suite_path inside the runner workspace")
    else:
        target = owned(conn, db.traces, ident, spec.payload.get("trace_id", ""))
        evaluator = version(conn, ident, spec.payload.get("evaluator_id", ""), spec.payload.get("version"))
        if not spec.payload.get("version") or target["project_id"] != project_id or evaluator["project_id"] != project_id or evaluator["kind"] != "evaluator":
            fail(422, "Evaluation jobs must pin a same-project evaluator version and trace")
    limits = proj["settings"].get("limits", {})
    estimate = round(spec.estimated_cost_usd * 1000000)
    now = time.time()
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    reservations = db.rows(conn, db.scoped(db.jobs, ident.tenant_id).where(db.jobs.c.project_id == project_id,
        sa.or_(db.jobs.c.state.in_(["queued", "leased"]), db.jobs.c.created_at >= today)))
    total = sum(r["estimated_microusd"] if r["state"] in {"queued", "leased"} else (r["actual_microusd"] or 0) for r in reservations)
    budget = limits.get("cost_microusd_daily")
    if budget is not None and total + estimate > budget:
        fail(429, "Experiment cost estimate exceeds available project budget")
    meter(conn, ident, project_id, "jobs", limit=limits.get("jobs_daily"))
    item = dict(id=db.uid(), tenant_id=ident.tenant_id, project_id=project_id, kind=spec.kind,
        payload=spec.payload, labels=spec.labels, state="queued", priority=spec.priority, attempt=0,
        max_attempts=spec.max_attempts, not_before=max(now, spec.not_before), lease_until=None, lease_token=None,
        worker=None, result=None, error=None, estimated_microusd=estimate, actual_microusd=None,
        idempotency_key=spec.idempotency_key, created_at=now)
    conn.execute(db.jobs.insert().values(**item))
    db.audit_event(conn, ident.tenant_id, ident.id, "job.enqueued", item["id"], {"kind": spec.kind, "estimated_microusd": estimate})
    return item


def register_runner(conn, ident, body: schemas.RunnerRegister):
    ident.require("runner")
    item = dict(id=db.uid(), tenant_id=ident.tenant_id, principal_id=ident.id, name=body.name,
                labels=body.labels, last_seen=time.time(), created_at=time.time())
    conn.execute(db.runners.insert().values(**item))
    db.audit_event(conn, ident.tenant_id, ident.id, "runner.registered", item["id"])
    return item


def check_runner(conn, ident, runner_id):
    ident.require("runner")
    r = db.row(conn, db.scoped(db.runners, ident.tenant_id).where(db.runners.c.id == runner_id,
        db.runners.c.principal_id == ident.id))
    if not r:
        fail(404, "Runner not found for this credential")
    return r


def claim(conn, ident, body: schemas.Claim, settings) -> dict | None:
    r = check_runner(conn, ident, body.runner_id)
    now = time.time()
    conn.execute(db.runners.update().where(db.runners.c.id == r["id"]).values(last_seen=now))
    query = db.scoped(db.jobs, ident.tenant_id).where(db.jobs.c.kind.in_(body.kinds),
        sa.or_(sa.and_(db.jobs.c.state == "queued", db.jobs.c.not_before <= now),
               sa.and_(db.jobs.c.state == "leased", db.jobs.c.lease_until < now)))
    if ident.projects:
        query = query.where(db.jobs.c.project_id.in_(ident.projects))
    candidates = conn.execute(query.order_by(db.jobs.c.priority.desc(), db.jobs.c.created_at, db.jobs.c.id)
                              .with_for_update(skip_locked=True).execution_options(yield_per=100)).mappings()
    for candidate in candidates:
        item = dict(candidate)
        if not set(item["labels"]).issubset(r["labels"]):
            continue
        if item["attempt"] >= item["max_attempts"]:
            conn.execute(db.jobs.update().where(db.jobs.c.id == item["id"]).values(state="failed", error="Lease attempts exhausted", lease_token=None, lease_until=None))
            continue
        item.update(state="leased", worker=r["id"], lease_token=random_secrets.token_urlsafe(32),
                    lease_until=now + settings.lease_seconds, attempt=item["attempt"] + 1)
        conn.execute(db.jobs.update().where(db.jobs.c.id == item["id"]).values(**item))
        db.audit_event(conn, ident.tenant_id, ident.id, "job.claimed", item["id"], {"runner_id": r["id"], "attempt": item["attempt"]})
        return item
    return None


def validate_lease(conn, ident, job_id, lease: schemas.Lease, *, completed_ok=False):
    check_runner(conn, ident, lease.runner_id)
    job = owned(conn, db.jobs, ident, job_id, "runner")
    if job["worker"] != lease.runner_id or not job["lease_token"] or not random_secrets.compare_digest(job["lease_token"], lease.lease_token):
        fail(409, "Stale or foreign job lease")
    if completed_ok and job["state"] == "succeeded":
        return job
    if job["state"] != "leased" or job["lease_until"] <= time.time():
        fail(409, "Job lease expired, cancelled, or no longer active")
    return job


def complete(conn, ident, job_id, body: schemas.Complete, settings: Settings):
    job = validate_lease(conn, ident, job_id, body, completed_ok=True)
    actual = round(body.actual_cost_usd * 1000000)
    clean = redact_for_project(body.result, project(conn, ident, job["project_id"], "runner"))
    if job["state"] == "succeeded":
        if job["result"] != clean or job["actual_microusd"] != actual:
            fail(409, "Job already completed with a different result")
        return job
    if job["kind"] == "suite":
        report = clean.get("report", clean)
        if not isinstance(report.get("results"), list) or not report["results"]:
            fail(422, "Suite completion requires a nonempty report.results list")
        # Persist output as an immutable experiment so a completed job is inspectable independently.
        system = Identity(ident.id, ident.tenant_id, "admin", "service", "runner", (), frozenset({"*"}))
        create_resource(conn, system, job["project_id"], schemas.ResourceCreate(kind="experiment", name="job-" + job_id,
            data={"report": report, "job_id": job_id, "payload": job["payload"]}), settings=settings, emit_finished=False)
        for result in report["results"]:
            # Agent SDK result trajectories are ingested into the same trace explorer.
            trace = schemas.TraceIn(trace_id=db.digest([job_id, result.get("scenario"), result.get("case_id", "default"), result.get("repetition", 0)]),
                name=str(result.get("scenario", "scenario")), status=result.get("verdict", "unset"),
                agent_version=report.get("agent_version") or report.get("agent", {}).get("version"), duration_ms=result.get("duration_ms", 0),
                trajectory=result.get("trajectory", {"events": []}), usage=result.get("usage", {}), checks=result.get("checks", []),
                metadata={"job_id": job_id, "agent_name": report.get("agent_name") or report.get("agent", {}).get("name")})
            ingest_trace(conn, system, job["project_id"], trace, settings)
    if job["kind"] == "evaluate" and clean.get("persisted_evaluation_id"):
        persisted = owned(conn, db.evaluations, ident, clean["persisted_evaluation_id"], "runner")
        if (persisted["trace_id"] != job["payload"]["trace_id"] or
            persisted["evaluator_id"] != job["payload"]["evaluator_id"] or
            persisted["evaluator_version"] != job["payload"]["version"]):
            fail(422, "Persisted evaluation does not match this job")
    if job["kind"] == "evaluate" and not clean.get("persisted_evaluation_id"):
        evaluation = clean.get("evaluation", {})
        if evaluation.get("verdict") not in {"pass", "fail", "incomplete", "error"}:
            fail(422, "Evaluation completion requires a valid verdict")
        trace = owned(conn, db.traces, ident, job["payload"]["trace_id"], "runner")
        conn.execute(db.evaluations.insert().values(id=db.uid(), tenant_id=ident.tenant_id, project_id=job["project_id"],
            trace_id=trace["id"], evaluator_id=job["payload"]["evaluator_id"], evaluator_version=job["payload"]["version"],
            result={**evaluation, "trace_hash": clean.get("trace_hash", trace["content_hash"]), "source": "customer-runner"}, created_at=time.time()))
        meter(conn, ident, job["project_id"], "evaluations", 1)
    conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(state="succeeded", result=clean,
        actual_microusd=actual, lease_until=None))
    meter(conn, ident, job["project_id"], "cost_microusd", actual)
    db.audit_event(conn, ident.tenant_id, ident.id, "job.completed", job_id, {"actual_microusd": actual})
    report = clean.get("report", {})
    report_results = report.get("results", [])
    passed = all(r.get("verdict") == "pass" for r in report_results) if report_results else clean.get("evaluation", {}).get("verdict") == "pass"
    emit_event(conn, ident, job["project_id"], "experiment.finished", {"job_id": job_id, "actual_cost_usd": body.actual_cost_usd, "passed": passed, "git_sha": job["payload"].get("git_sha"), "automation_depth": job["payload"].get("_automation_depth", 0)})
    if actual > job["estimated_microusd"]:
        emit_event(conn, ident, job["project_id"], "budget.estimate_exceeded", {"job_id": job_id, "actual_microusd": actual, "estimated_microusd": job["estimated_microusd"]})
    return owned(conn, db.jobs, ident, job_id, "runner")


def get_secret(conn, ident, project_id, name, vault: Vault, *, internal=False) -> str:
    project(conn, ident, project_id, "read" if internal else "secret")
    item = db.row(conn, db.scoped(db.secrets, ident.tenant_id).where(db.secrets.c.project_id == project_id, db.secrets.c.name == name))
    if not item:
        fail(404, "Credential reference not found")
    db.audit_event(conn, ident.tenant_id, ident.id, "secret.used", item["id"], {"internal": internal})
    return vault.decrypt(item["ciphertext"], f"{ident.tenant_id}:{project_id}:{name}")
