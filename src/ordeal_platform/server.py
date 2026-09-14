from __future__ import annotations

import base64
from contextlib import asynccontextmanager
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import jsonschema
import sqlalchemy as sa
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError

from . import __version__, behavior, db, otlp, schemas, services
from .config import Settings
from .evaluators import evaluate
from .identity import router as identity_router
from .middleware import SecurityMiddleware
from .scim import router as scim_router
from .security import Identity, ROLE_PERMISSIONS, PRIVILEGED_KINDS, Vault, fail, identity, issue_token, redact
from .storage import ArtifactStore

logger = logging.getLogger("ordeal.server")


def clean_job(item):
    return {k: v for k, v in item.items() if k != "lease_token"}


def page(conn, query, *, offset=0, limit=100):
    count = conn.execute(sa.select(sa.func.count()).select_from(query.subquery())).scalar()
    items = db.rows(conn, query.offset(offset).limit(limit))
    return {"items": items, "total": count, "offset": offset, "limit": limit, "has_more": offset + len(items) < count}


def create_app(settings: Settings | None = None, database: db.Database | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    database = database or db.Database(settings.database_url)
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def lifespan(app):
        database.check()
        yield

    app = FastAPI(title="Ordeal Agent Behavior Platform", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url="/openapi.json")
    app.state.settings, app.state.database = settings, database
    app.state.vault, app.state.store = Vault(settings.master_key), ArtifactStore(settings)
    app.add_middleware(SecurityMiddleware, settings=settings)
    app.include_router(identity_router)
    app.include_router(scim_router)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errors = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return JSONResponse({"detail": errors}, status_code=422)

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        return JSONResponse({"detail": "Resource conflicts with an existing record"}, status_code=409)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)[:500]}, status_code=422)

    @app.exception_handler(jsonschema.ValidationError)
    async def schema_error(request, exc):
        return JSONResponse({"detail": "Data does not match the configured JSON Schema", "path": list(exc.path)}, status_code=422)

    @app.exception_handler(jsonschema.SchemaError)
    async def invalid_schema(request, exc):
        return JSONResponse({"detail": "Invalid JSON Schema"}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.error("Unexpected request failure type=%s request_id=%s", type(exc).__name__, getattr(request.state, "request_id", ""))
        return JSONResponse({"detail": "Internal error", "request_id": getattr(request.state, "request_id", "")}, status_code=500)

    @app.get("/healthz")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    def ready():
        try:
            database.check()
            return {"status": "ready"}
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics(ident: Identity = Depends(identity)):
        ident.require_org("audit")
        with database.tx(ident.tenant_id) as conn:
            job_counts = dict(conn.execute(sa.select(db.jobs.c.state, sa.func.count()).where(
                db.jobs.c.tenant_id == ident.tenant_id).group_by(db.jobs.c.state)).all())
            trace_count = conn.execute(sa.select(sa.func.count()).select_from(db.traces).where(db.traces.c.tenant_id == ident.tenant_id)).scalar()
            text = ["# HELP ordeal_traces_stored Stored traces for the authenticated organization", "# TYPE ordeal_traces_stored gauge", f"ordeal_traces_stored {trace_count}", "# TYPE ordeal_jobs gauge"]
            for state in ("queued", "leased", "succeeded", "failed", "cancelled"):
                text.append(f'ordeal_jobs{{state="{state}"}} {job_counts.get(state, 0)}')
            pending = conn.execute(sa.select(sa.func.count()).select_from(db.deliveries).where(db.deliveries.c.tenant_id == ident.tenant_id, db.deliveries.c.state.in_(["pending", "delivering"]))).scalar()
            text.extend(["# TYPE ordeal_webhook_pending gauge", f"ordeal_webhook_pending {pending}"])
        return "\n".join(text) + "\n"

    @app.get("/api/v1/me")
    def me(ident: Identity = Depends(identity)):
        return {"id": ident.id, "tenant_id": ident.tenant_id, "name": ident.name, "role": ident.role,
                "kind": ident.kind, "projects": ident.projects, "permissions": sorted(ident.permissions), "version": __version__}

    @app.get("/api/v1/projects")
    def list_projects(ident: Identity = Depends(identity)):
        ident.require("read")
        with database.tx(ident.tenant_id) as conn:
            query = db.scoped(db.projects, ident.tenant_id)
            if ident.projects:
                query = query.where(db.projects.c.id.in_(ident.projects))
            return {"items": db.rows(conn, query.order_by(db.projects.c.name))}

    @app.post("/api/v1/projects", status_code=201)
    def create_project(body: schemas.ProjectCreate, ident: Identity = Depends(identity)):
        ident.require_org("identity")
        config = schemas.ProjectSettings.model_validate(body.settings).model_dump()
        if config["online_evaluators"]:
            fail(422, "Configure online evaluators after the project has been created")
        item = dict(id=db.uid(), tenant_id=ident.tenant_id, name=body.name, settings=config, created_at=time.time())
        with database.tx(ident.tenant_id) as conn:
            conn.execute(db.projects.insert().values(**item))
            db.audit_event(conn, ident.tenant_id, ident.id, "project.created", item["id"])
        return item

    @app.put("/api/v1/projects/{project_id}/settings")
    def project_settings(project_id: str, body: schemas.ProjectSettings, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.project(conn, ident, project_id, "identity")
            for evaluator in body.online_evaluators:
                ref = services.version(conn, ident, evaluator["id"], evaluator["version"])
                if ref["project_id"] != project_id or ref["kind"] != "evaluator":
                    fail(422, "Invalid online evaluator reference")
            conn.execute(db.projects.update().where(db.projects.c.id == project_id).values(settings=body.model_dump()))
            db.audit_event(conn, ident.tenant_id, ident.id, "project.settings.updated", project_id, {"fields": list(body.model_fields_set)})
            return services.project(conn, ident, project_id)

    @app.get("/api/v1/principals")
    def list_principals(ident: Identity = Depends(identity)):
        ident.require_org("identity")
        with database.tx(ident.tenant_id) as conn:
            return {"items": db.rows(conn, db.scoped(db.principals, ident.tenant_id).order_by(db.principals.c.created_at))}

    @app.post("/api/v1/principals", status_code=201)
    def create_principal(body: schemas.PrincipalCreate, ident: Identity = Depends(identity)):
        ident.require_org("identity")
        if body.role == "owner" and ident.role != "owner":
            fail(403, "Only an owner can create another owner")
        if body.permissions and not set(body.permissions).issubset(ROLE_PERMISSIONS[body.role]) and "*" not in ROLE_PERMISSIONS[body.role]:
            fail(422, "Service permissions must be a subset of the assigned role")
        if body.kind == "runner" and body.role != "runner":
            fail(422, "Runner identities must use the runner role")
        with database.tx(ident.tenant_id) as conn:
            for pid in body.projects:
                services.project(conn, ident, pid)
            item = dict(id=db.uid(), tenant_id=ident.tenant_id, name=body.name,
                username=body.username.strip().lower() if body.username else None, external_id=None, role=body.role, kind=body.kind,
                projects=body.projects, permissions=body.permissions, active=True, created_at=time.time())
            conn.execute(db.principals.insert().values(**item))
            token, info = issue_token(conn, item, "initial", body.token_days)
            db.audit_event(conn, ident.tenant_id, ident.id, "principal.created", item["id"], {"role": body.role, "kind": body.kind})
            return {**item, "token": token, "token_info": info}

    @app.patch("/api/v1/principals/{principal_id}")
    def update_principal(principal_id: str, body: schemas.PrincipalUpdate, ident: Identity = Depends(identity)):
        ident.require_org("identity")
        with database.tx(ident.tenant_id) as conn:
            conn.execute(sa.select(db.tenants.c.id).where(db.tenants.c.id == ident.tenant_id).with_for_update())
            item = services.owned(conn, db.principals, ident, principal_id, "identity")
            if item["role"] == "owner":
                if ident.role != "owner":
                    fail(403, "Only an owner can modify an owner")
                count = conn.execute(sa.select(sa.func.count()).select_from(db.principals).where(db.principals.c.tenant_id == ident.tenant_id,
                    db.principals.c.role == "owner", db.principals.c.active == True)).scalar()
                if not body.active and count <= 1:
                    fail(409, "Cannot deactivate the last owner")
            conn.execute(db.principals.update().where(db.principals.c.id == principal_id).values(active=body.active))
            if not body.active:
                conn.execute(db.tokens.update().where(db.tokens.c.principal_id == principal_id).values(revoked=True))
            db.audit_event(conn, ident.tenant_id, ident.id, "principal.updated", principal_id, {"active": body.active})
            return {**item, "active": body.active}

    @app.get("/api/v1/tokens")
    def list_tokens(ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            query = db.scoped(db.tokens, ident.tenant_id)
            if "identity" not in ident.permissions and "*" not in ident.permissions:
                query = query.where(db.tokens.c.principal_id == ident.id)
            elif ident.projects:
                query = query.where(db.tokens.c.principal_id == ident.id)
            return {"items": [{k: v for k, v in r.items() if k != "token_hash"} for r in db.rows(conn, query)]}

    @app.post("/api/v1/principals/{principal_id}/tokens", status_code=201)
    def rotate_token(principal_id: str, body: schemas.TokenCreate, ident: Identity = Depends(identity)):
        if principal_id != ident.id:
            ident.require_org("identity")
        with database.tx(ident.tenant_id) as conn:
            p = db.row(conn, db.scoped(db.principals, ident.tenant_id).where(db.principals.c.id == principal_id, db.principals.c.active == True))
            if not p:
                fail(404, "Principal not found")
            if p["role"] == "owner" and ident.role != "owner":
                fail(403, "Only an owner can issue owner credentials")
            token, info = issue_token(conn, p, body.label, body.days)
            db.audit_event(conn, ident.tenant_id, ident.id, "token.issued", info["id"])
            return {"token": token, **info}

    @app.delete("/api/v1/tokens/{token_id}")
    def revoke_token(token_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            token = db.row(conn, db.scoped(db.tokens, ident.tenant_id).where(db.tokens.c.id == token_id))
            if not token:
                fail(404, "Token not found")
            if token["principal_id"] != ident.id:
                ident.require_org("identity")
                target = db.row(conn, db.scoped(db.principals, ident.tenant_id).where(db.principals.c.id == token["principal_id"]))
                if target["role"] == "owner" and ident.role != "owner":
                    fail(403, "Only an owner can revoke owner credentials")
            conn.execute(db.tokens.update().where(db.tokens.c.id == token_id).values(revoked=True))
            db.audit_event(conn, ident.tenant_id, ident.id, "token.revoked", token_id)
        return {"revoked": True}

    @app.get("/api/v1/projects/{project_id}/resources")
    def list_resources(project_id: str, kind: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200), ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.project(conn, ident, project_id)
            query = db.scoped(db.resources, ident.tenant_id).where(db.resources.c.project_id == project_id, db.resources.c.deleted == False)
            if kind:
                query = query.where(db.resources.c.kind == kind)
            return page(conn, query.order_by(db.resources.c.created_at.desc()), offset=offset, limit=limit)

    @app.post("/api/v1/projects/{project_id}/resources", status_code=201)
    def create_resource(project_id: str, body: schemas.ResourceCreate, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            return services.create_resource(conn, ident, project_id, body, settings)

    @app.get("/api/v1/resources/{resource_id}")
    def get_resource(resource_id: str, version: int | None = Query(None, ge=1), ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            return services.version(conn, ident, resource_id, version)

    @app.get("/api/v1/resources/{resource_id}/versions")
    def list_versions(resource_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.owned(conn, db.resources, ident, resource_id)
            return {"items": db.rows(conn, db.scoped(db.versions, ident.tenant_id).where(db.versions.c.resource_id == resource_id).order_by(db.versions.c.version.desc()).limit(500))}

    @app.post("/api/v1/resources/{resource_id}/versions", status_code=201)
    def add_version(resource_id: str, body: schemas.VersionCreate, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            return services.add_version(conn, ident, resource_id, body, settings)

    @app.post("/api/v1/resources/{resource_id}/promote")
    def promote(resource_id: str, body: schemas.Promote, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            resource = services.version(conn, ident, resource_id, body.version, "identity" if body.environment == "production" else "write")
            aliases = {**resource["aliases"], body.environment: body.version}
            conn.execute(db.resources.update().where(db.resources.c.id == resource_id).values(aliases=aliases))
            db.audit_event(conn, ident.tenant_id, ident.id, "resource.promoted", resource_id, body.model_dump())
            services.emit_event(conn, ident, resource["project_id"], "deployment.changed", {"resource_id": resource_id, **body.model_dump()})
            return {"id": resource_id, "aliases": aliases}

    @app.get("/api/v1/resources/{resource_id}/diff")
    def resource_diff(resource_id: str, before: int = Query(..., ge=1), after: int = Query(..., ge=1), ident: Identity = Depends(identity)):
        from .operations import json_diff
        with database.tx(ident.tenant_id) as conn:
            a, b = services.version(conn, ident, resource_id, before), services.version(conn, ident, resource_id, after)
            return {"before_hash": a["content_hash"], "after_hash": b["content_hash"], "changes": json_diff(a["data"], b["data"])}

    @app.delete("/api/v1/resources/{resource_id}")
    def delete_resource(resource_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            resource = services.owned(conn, db.resources, ident, resource_id)
            ident.require(PRIVILEGED_KINDS.get(resource["kind"], "write"), resource["project_id"])
            conn.execute(db.resources.update().where(db.resources.c.id == resource_id).values(deleted=True))
            db.audit_event(conn, ident.tenant_id, ident.id, "resource.deleted", resource_id)
        return {"deleted": True, "historical_versions_retained": True}

    @app.post("/api/v1/projects/{project_id}/traces")
    def ingest_trace(project_id: str, body: schemas.TraceIn, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            item, created = services.ingest_trace(conn, ident, project_id, body, settings)
            return {"id": item["id"], "trace_id": item["trace_id"], "content_hash": item["content_hash"], "created": created}

    @app.get("/api/v1/projects/{project_id}/traces")
    def list_traces(project_id: str, environment: str | None = None, status: str | None = None, agent_version: str | None = None,
                    search: str | None = Query(None, max_length=200), since: float | None = None,
                    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.project(conn, ident, project_id)
            query = db.scoped(db.traces, ident.tenant_id).where(db.traces.c.project_id == project_id)
            for column, value in ((db.traces.c.environment, environment), (db.traces.c.status, status), (db.traces.c.agent_version, agent_version)):
                if value is not None:
                    query = query.where(column == value)
            if search:
                query = query.where(db.traces.c.name.ilike("%" + search.replace("%", "\\%").replace("_", "\\_") + "%", escape="\\"))
            if since is not None:
                query = query.where(db.traces.c.created_at >= since)
            result = page(conn, query.order_by(db.traces.c.created_at.desc()), offset=offset, limit=limit)
            result["items"] = [{k: v for k, v in r.items() if k != "data"} for r in result["items"]]
            return result

    @app.post("/api/v1/resources/{resource_id}/render")
    def render_prompt(resource_id: str, body: schemas.PromptRender, ident: Identity = Depends(identity)):
        from .prompts import render
        if (body.version is None) == (body.environment is None):
            fail(422, "Pin either a version or an environment alias")
        with database.tx(ident.tenant_id) as conn:
            obj = services.owned(conn, db.resources, ident, resource_id)
            number = body.version if body.version is not None else obj["aliases"].get(body.environment)
            if number is None:
                fail(404, "Prompt environment alias has not been promoted")
            selected = services.version(conn, ident, resource_id, number)
            if selected["kind"] != "prompt":
                fail(422, "Resource is not a prompt")
            text = render(selected["data"]["template"], body.variables)
            db.audit_event(conn, ident.tenant_id, ident.id, "prompt.rendered", resource_id, {"version": number})
            return {"id": resource_id, "version": number, "content_hash": selected["content_hash"], "text": text}

    @app.get("/api/v1/traces/{trace_id}")
    def get_trace(trace_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            item = services.owned(conn, db.traces, ident, trace_id)
            db.audit_event(conn, ident.tenant_id, ident.id, "trace.viewed", trace_id)
            item["evaluations"] = db.rows(conn, db.scoped(db.evaluations, ident.tenant_id).where(db.evaluations.c.trace_id == trace_id).order_by(db.evaluations.c.created_at))
            return item

    @app.get("/api/v1/traces/{trace_id}/export")
    def export_trace(trace_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            item = services.owned(conn, db.traces, ident, trace_id, "export")
            db.audit_event(conn, ident.tenant_id, ident.id, "trace.exported", trace_id)
            return JSONResponse(item["data"], headers={"Content-Disposition": f'attachment; filename="trace-{trace_id}.json"'})

    @app.get("/api/v1/traces/{trace_id}/otlp")
    def export_otlp(trace_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            item = services.owned(conn, db.traces, ident, trace_id, "export")
            if item["data"].get("metadata", {}).get("source") != "otlp":
                fail(422, "This trace has no original OTLP span envelope; export native JSON instead")
            db.audit_event(conn, ident.tenant_id, ident.id, "trace.otlp_exported", trace_id)
            return otlp.export_document(item)

    @app.post("/api/v1/traces/{trace_id}/legal-hold")
    def hold_trace(trace_id: str, hold: bool = True, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.owned(conn, db.traces, ident, trace_id, "identity")
            conn.execute(db.traces.update().where(db.traces.c.id == trace_id).values(legal_hold=hold))
            db.audit_event(conn, ident.tenant_id, ident.id, "trace.legal_hold", trace_id, {"hold": hold})
        return {"legal_hold": hold}

    @app.delete("/api/v1/traces/{trace_id}")
    def delete_trace(trace_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            item = services.owned(conn, db.traces, ident, trace_id, "identity")
            if item["legal_hold"]:
                fail(409, "Trace is under legal hold")
            for table in (db.evaluations, db.reviews):
                conn.execute(table.delete().where(table.c.tenant_id == ident.tenant_id, table.c.trace_id == trace_id))
            conn.execute(db.traces.delete().where(db.traces.c.id == trace_id))
            db.audit_event(conn, ident.tenant_id, ident.id, "trace.deleted", trace_id)
        return {"deleted": True, "note": "Previously materialized dataset versions and exports have independent retention"}

    @app.post("/api/v1/traces/{trace_id}/dataset")
    def trace_to_dataset(trace_id: str, body: schemas.TraceToDataset, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            trace = services.owned(conn, db.traces, ident, trace_id, "export")
            dataset = services.version(conn, ident, body.dataset_id, permission="write")
            if dataset["kind"] != "dataset" or dataset["project_id"] != trace["project_id"]:
                fail(422, "Target must be a dataset in the same project")
            case = services.trace_case(trace, body.instruction, body.expected)
            data = {**dataset["data"], "cases": dataset["data"].get("cases", []) + [case]}
            updated = services.add_version(conn, ident, body.dataset_id, schemas.VersionCreate(base_version=body.base_version, data=data), settings)
            return {"dataset": updated, "case": case}

    @app.post("/api/v1/traces/{trace_id}/evaluate")
    def evaluate_trace(trace_id: str, body: schemas.EvaluateRequest, ident: Identity = Depends(identity)):
        from .operations import evaluate_trace as perform
        return perform(database, settings, ident, trace_id, body.evaluator_id, body.version)

    @app.post("/api/v1/evaluators/calibrate")
    def calibrate(body: list[dict], ident: Identity = Depends(identity)):
        ident.require("read")
        return behavior.calibration(body)

    @app.post("/api/v1/experiments/compare")
    def compare(body: schemas.CompareRequest, ident: Identity = Depends(identity)):
        ident.require("read")
        return behavior.compare_reports(body.baseline, body.candidate, body.min_pass_rate, body.max_pass_rate_drop)

    @app.get("/api/v1/projects/{project_id}/analytics")
    def analytics(project_id: str, since: float | None = None, environment: str | None = None, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.project(conn, ident, project_id)
            query = db.scoped(db.traces, ident.tenant_id).where(db.traces.c.project_id == project_id)
            if since is not None:
                query = query.where(db.traces.c.created_at >= since)
            if environment:
                query = query.where(db.traces.c.environment == environment)
            count = conn.execute(sa.select(sa.func.count()).select_from(query.subquery())).scalar()
            records = db.rows(conn, query.order_by(db.traces.c.created_at.desc()).limit(10000))
            return {**behavior.analytics(records), "matched_traces": count, "sample_limit": 10000, "truncated": count > len(records)}

    @app.get("/api/v1/projects/{project_id}/behavior/compare")
    def compare_behavior(project_id: str, baseline_version: str, candidate_version: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.project(conn, ident, project_id)
            query = db.scoped(db.traces, ident.tenant_id).where(db.traces.c.project_id == project_id)
            a = db.rows(conn, query.where(db.traces.c.agent_version == baseline_version).order_by(db.traces.c.created_at.desc()).limit(5000))
            b = db.rows(conn, query.where(db.traces.c.agent_version == candidate_version).order_by(db.traces.c.created_at.desc()).limit(5000))
            return {**behavior.drift(a, b), "cohort_limit": 5000}

    @app.post("/v1/traces")
    async def ingest_otlp(request: Request, ident: Identity = Depends(identity)):
        project_id = request.headers.get("x-ordeal-project", "")
        content_type = request.headers.get("content-type", "").split(";")[0].lower()
        raw = await request.body()
        if content_type == "application/x-protobuf":
            try:
                document = otlp.decode_protobuf(raw)
            except Exception:
                fail(400, "Invalid protobuf trace request")
        elif content_type == "application/json":
            document = json.loads(raw)
        else:
            fail(415, "Use application/json or application/x-protobuf")
        with database.tx(ident.tenant_id) as conn:
            otlp.ingest(conn, ident, project_id, document, settings)
        if content_type == "application/x-protobuf":
            from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceResponse
            return Response(ExportTraceServiceResponse().SerializeToString(), media_type=content_type)
        return {}

    @app.post("/api/v1/projects/{project_id}/jobs", status_code=201)
    def enqueue(project_id: str, body: schemas.JobCreate, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            return clean_job(services.enqueue(conn, ident, project_id, body))

    @app.get("/api/v1/projects/{project_id}/jobs")
    def list_jobs(project_id: str, state: str | None = None, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200), ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.project(conn, ident, project_id)
            query = db.scoped(db.jobs, ident.tenant_id).where(db.jobs.c.project_id == project_id)
            if state:
                query = query.where(db.jobs.c.state == state)
            result = page(conn, query.order_by(db.jobs.c.created_at.desc()), offset=offset, limit=limit)
            result["items"] = [clean_job(r) for r in result["items"]]
            return result

    @app.get("/api/v1/jobs/{job_id}")
    def get_job(job_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            return clean_job(services.owned(conn, db.jobs, ident, job_id))

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            job = services.owned(conn, db.jobs, ident, job_id, "execute")
            if job["state"] in {"succeeded", "failed", "cancelled"}:
                fail(409, "Job is already terminal")
            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(state="cancelled", lease_token=None, lease_until=None))
            db.audit_event(conn, ident.tenant_id, ident.id, "job.cancelled", job_id)
        return {"state": "cancelled"}

    @app.post("/api/v1/runners", status_code=201)
    def register_runner(body: schemas.RunnerRegister, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            return services.register_runner(conn, ident, body)

    @app.get("/api/v1/runners")
    def list_runners(ident: Identity = Depends(identity)):
        ident.require("runner")
        with database.tx(ident.tenant_id) as conn:
            query = db.scoped(db.runners, ident.tenant_id)
            if ident.role == "runner" or ident.projects:
                query = query.where(db.runners.c.principal_id == ident.id)
            return {"items": db.rows(conn, query)}

    @app.post("/api/v1/jobs/claim")
    def claim_job(body: schemas.Claim, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            job = services.claim(conn, ident, body, settings)
            return {"job": job}

    @app.post("/api/v1/jobs/{job_id}/heartbeat")
    def heartbeat(job_id: str, body: schemas.Lease, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.validate_lease(conn, ident, job_id, body)
            until = time.time() + settings.lease_seconds
            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(lease_until=until))
            return {"lease_until": until}

    @app.post("/api/v1/jobs/{job_id}/complete")
    def complete_job(job_id: str, body: schemas.Complete, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            return clean_job(services.complete(conn, ident, job_id, body, settings))

    @app.post("/api/v1/jobs/{job_id}/fail")
    def fail_job(job_id: str, body: schemas.JobFailure, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            job = services.validate_lease(conn, ident, job_id, body)
            state = "queued" if body.retryable and job["attempt"] < job["max_attempts"] else "failed"
            message = redact(body.message)
            conn.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(state=state, error=message,
                not_before=time.time() + min(300, 2 ** job["attempt"]), lease_token=None, lease_until=None))
            db.audit_event(conn, ident.tenant_id, ident.id, "job.attempt_failed", job_id, {"state": state, "attempt": job["attempt"]})
            return {"state": state}

    @app.post("/api/v1/jobs/{job_id}/credentials")
    def job_credentials(job_id: str, body: schemas.Lease, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            job = services.validate_lease(conn, ident, job_id, body)
            refs = job["payload"].get("secret_refs", {})
            if not isinstance(refs, dict) or len(refs) > 20:
                fail(422, "secret_refs must map environment variables to credential names")
            out = {}
            import re
            for env_name, secret_name in refs.items():
                if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", env_name) or env_name.startswith(("LD_", "PYTHON", "PATH", "HOME")):
                    fail(422, "Unsafe secret environment variable name")
                out[env_name] = services.get_secret(conn, ident, job["project_id"], secret_name, app.state.vault, internal=True)
            return {"environment": out}

    @app.post("/api/v1/projects/{project_id}/secrets", status_code=201)
    def set_secret(project_id: str, body: schemas.SecretCreate, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.project(conn, ident, project_id, "secret")
            context = f"{ident.tenant_id}:{project_id}:{body.name}"
            item = db.row(conn, db.scoped(db.secrets, ident.tenant_id).where(db.secrets.c.project_id == project_id, db.secrets.c.name == body.name))
            if item:
                conn.execute(db.secrets.update().where(db.secrets.c.id == item["id"]).values(ciphertext=app.state.vault.encrypt(body.value, context), key_id=settings.encryption_key_id))
                secret_id = item["id"]
            else:
                secret_id = db.uid()
                conn.execute(db.secrets.insert().values(id=secret_id, tenant_id=ident.tenant_id, project_id=project_id, name=body.name,
                    ciphertext=app.state.vault.encrypt(body.value, context), key_id=settings.encryption_key_id, created_at=time.time()))
            db.audit_event(conn, ident.tenant_id, ident.id, "secret.rotated" if item else "secret.created", secret_id)
            return {"id": secret_id, "name": body.name, "key_id": settings.encryption_key_id}

    @app.get("/api/v1/projects/{project_id}/secrets")
    def list_secrets(project_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.project(conn, ident, project_id, "secret")
            items = db.rows(conn, db.scoped(db.secrets, ident.tenant_id).where(db.secrets.c.project_id == project_id))
            return {"items": [{k: v for k, v in item.items() if k != "ciphertext"} for item in items]}

    @app.delete("/api/v1/secrets/{secret_id}")
    def delete_secret(secret_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.owned(conn, db.secrets, ident, secret_id, "secret")
            conn.execute(db.secrets.delete().where(db.secrets.c.id == secret_id))
            db.audit_event(conn, ident.tenant_id, ident.id, "secret.deleted", secret_id)
        return {"deleted": True}

    @app.post("/api/v1/projects/{project_id}/reviews", status_code=201)
    def create_review(project_id: str, body: schemas.ReviewCreate, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.project(conn, ident, project_id, "review")
            trace = services.owned(conn, db.traces, ident, body.trace_id)
            if trace["project_id"] != project_id:
                fail(422, "Trace must belong to this project")
            if body.assignee:
                _check_assignee(conn, ident, body.assignee, project_id)
            item = dict(id=db.uid(), tenant_id=ident.tenant_id, project_id=project_id, trace_id=body.trace_id,
                queue=body.queue, assignee=body.assignee, status="unreviewed", labels={}, comment="", created_at=time.time())
            conn.execute(db.reviews.insert().values(**item))
            db.audit_event(conn, ident.tenant_id, ident.id, "review.created", item["id"])
            return item

    def _check_assignee(conn, ident, assignee, project_id):
        target = db.row(conn, db.scoped(db.principals, ident.tenant_id).where(db.principals.c.id == assignee, db.principals.c.active == True))
        if not target or (target["projects"] and project_id not in target["projects"]):
            fail(422, "Assignee must be an active project member")
        perms = ROLE_PERMISSIONS.get(target["role"], set())
        if "*" not in perms and "review" not in perms:
            fail(422, "Assignee must have review permission")

    @app.get("/api/v1/projects/{project_id}/reviews")
    def list_reviews(project_id: str, status: str | None = None, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.project(conn, ident, project_id, "read")
            query = db.scoped(db.reviews, ident.tenant_id).where(db.reviews.c.project_id == project_id)
            if status:
                query = query.where(db.reviews.c.status == status)
            return {"items": db.rows(conn, query.order_by(db.reviews.c.created_at.desc()).limit(500))}

    @app.patch("/api/v1/reviews/{review_id}")
    def update_review(review_id: str, body: schemas.ReviewUpdate, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            item = services.owned(conn, db.reviews, ident, review_id, "review")
            if item["assignee"] and item["assignee"] != ident.id and ident.role not in {"owner", "admin"}:
                fail(403, "Review is assigned to another person")
            if body.assignee:
                _check_assignee(conn, ident, body.assignee, item["project_id"])
            updates = body.model_dump()
            updates["comment"] = redact(updates["comment"])
            updates["labels"] = redact(updates["labels"])
            conn.execute(db.reviews.update().where(db.reviews.c.id == review_id).values(**updates))
            db.audit_event(conn, ident.tenant_id, ident.id, "review.updated", review_id, {"status": body.status})
            return {**item, **updates}

    @app.post("/api/v1/projects/{project_id}/artifacts", status_code=201)
    async def upload_artifact(project_id: str, request: Request, ident: Identity = Depends(identity)):
        payload = await request.body()
        with database.tx(ident.tenant_id) as conn:
            proj = services.project(conn, ident, project_id, "write")
            services.meter(conn, ident, project_id, "artifact_bytes", len(payload), limit=proj["settings"].get("limits", {}).get("bytes_daily"))
            key, checksum = app.state.store.put(ident.tenant_id, payload)
            item = dict(id=db.uid(), tenant_id=ident.tenant_id, project_id=project_id, sha256=checksum, size=len(payload),
                media_type=request.headers.get("content-type", "application/octet-stream")[:128], path=key,
                key_id=settings.encryption_key_id, legal_hold=False, created_at=time.time())
            conn.execute(db.artifacts.insert().values(**item))
            db.audit_event(conn, ident.tenant_id, ident.id, "artifact.created", item["id"], {"sha256": checksum})
            return {k: v for k, v in item.items() if k != "path"}

    @app.get("/api/v1/artifacts/{artifact_id}")
    def download_artifact(artifact_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            item = services.owned(conn, db.artifacts, ident, artifact_id, "export")
            payload = app.state.store.get(item["path"])
            db.audit_event(conn, ident.tenant_id, ident.id, "artifact.exported", artifact_id)
            return Response(payload, media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="{artifact_id}.bin"', "X-Content-SHA256": item["sha256"]})

    @app.post("/api/v1/artifacts/{artifact_id}/legal-hold")
    def artifact_hold(artifact_id: str, hold: bool = True, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.owned(conn, db.artifacts, ident, artifact_id, "identity")
            conn.execute(db.artifacts.update().where(db.artifacts.c.id == artifact_id).values(legal_hold=hold))
            db.audit_event(conn, ident.tenant_id, ident.id, "artifact.legal_hold", artifact_id, {"hold": hold})
        return {"legal_hold": hold}

    @app.post("/api/v1/projects/{project_id}/cache")
    def cache_put(project_id: str, body: schemas.CachePut, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            proj = services.project(conn, ident, project_id, "write")
            key = db.digest([body.namespace, body.key])
            data = services.redact_for_project(body.value, proj)
            existing = db.row(conn, db.scoped(db.cache, ident.tenant_id).where(db.cache.c.project_id == project_id, db.cache.c.key == key))
            values = {"data": data, "expires_at": time.time() + body.ttl_seconds}
            if existing:
                conn.execute(db.cache.update().where(db.cache.c.id == existing["id"]).values(**values))
            else:
                conn.execute(db.cache.insert().values(id=db.uid(), tenant_id=ident.tenant_id, project_id=project_id, key=key, created_at=time.time(), **values))
            return {"key": key, "expires_at": values["expires_at"]}

    @app.get("/api/v1/projects/{project_id}/cache/{key}")
    def cache_get(project_id: str, key: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.project(conn, ident, project_id)
            item = db.row(conn, db.scoped(db.cache, ident.tenant_id).where(db.cache.c.project_id == project_id, db.cache.c.key == key, db.cache.c.expires_at > time.time()))
            if not item:
                fail(404, "Cache miss")
            return {"key": key, "value": item["data"], "expires_at": item["expires_at"]}

    @app.get("/api/v1/projects/{project_id}/alerts")
    def list_alerts(project_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.project(conn, ident, project_id)
            return {"items": db.rows(conn, db.scoped(db.alerts, ident.tenant_id).where(db.alerts.c.project_id == project_id).order_by(db.alerts.c.created_at.desc()).limit(500))}

    @app.post("/api/v1/alerts/{alert_id}/acknowledge")
    def acknowledge(alert_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.owned(conn, db.alerts, ident, alert_id, "write")
            conn.execute(db.alerts.update().where(db.alerts.c.id == alert_id).values(state="acknowledged"))
            db.audit_event(conn, ident.tenant_id, ident.id, "alert.acknowledged", alert_id)
        return {"state": "acknowledged"}

    @app.get("/api/v1/projects/{project_id}/deliveries")
    def list_deliveries(project_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            services.project(conn, ident, project_id, "secret")
            return {"items": [{k: v for k, v in item.items() if k != "lease_token"} for item in db.rows(conn,
                db.scoped(db.deliveries, ident.tenant_id).where(db.deliveries.c.project_id == project_id).order_by(db.deliveries.c.created_at.desc()).limit(500))]}

    @app.post("/api/v1/deliveries/{delivery_id}/retry")
    def retry_delivery(delivery_id: str, ident: Identity = Depends(identity)):
        with database.tx(ident.tenant_id) as conn:
            item = services.owned(conn, db.deliveries, ident, delivery_id, "secret")
            if item["state"] != "dead":
                fail(409, "Only dead-letter deliveries can be retried manually")
            conn.execute(db.deliveries.update().where(db.deliveries.c.id == delivery_id).values(state="pending", attempt=0, not_before=time.time(), lease_token=None, lease_until=None))
            db.audit_event(conn, ident.tenant_id, ident.id, "delivery.retried", delivery_id)
        return {"state": "pending"}

    @app.post("/api/v1/maintenance")
    def maintenance(ident: Identity = Depends(identity)):
        ident.require_org("identity")
        from .operations import maintenance
        return maintenance(database, settings, ident)

    @app.get("/api/v1/audit")
    def audit_log(offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500), ident: Identity = Depends(identity)):
        ident.require_org("audit")
        with database.tx(ident.tenant_id) as conn:
            return page(conn, db.scoped(db.audit, ident.tenant_id).order_by(db.audit.c.sequence.desc()), offset=offset, limit=limit)

    @app.get("/api/v1/audit/checkpoint")
    def audit_checkpoint(ident: Identity = Depends(identity)):
        ident.require_org("audit")
        from .operations import checkpoint
        return checkpoint(database, settings, ident)

    @app.get("/api/v1/usage")
    def usage(period: str | None = None, ident: Identity = Depends(identity)):
        ident.require_org("billing")
        with database.tx(ident.tenant_id) as conn:
            query = db.scoped(db.usage, ident.tenant_id)
            if period:
                query = query.where(db.usage.c.period.startswith(period))
            return {"items": db.rows(conn, query.order_by(db.usage.c.period.desc()).limit(10000))}

    @app.post("/api/v1/billing/invoices", status_code=201)
    def create_invoice(body: schemas.InvoiceCreate, ident: Identity = Depends(identity)):
        ident.require_org("billing")
        if any(type(v) is not int or v < 0 or v > 10 ** 12 for v in body.rates_microusd.values()):
            fail(422, "Rates must be nonnegative integer micro-USD per unit")
        with database.tx(ident.tenant_id) as conn:
            existing = db.row(conn, db.scoped(db.invoices, ident.tenant_id).where(db.invoices.c.period == body.period))
            if existing:
                if existing["data"]["rates_microusd"] != body.rates_microusd:
                    fail(409, "An invoice already exists for this period with different rates")
                return existing
            rows = db.rows(conn, db.scoped(db.usage, ident.tenant_id).where(db.usage.c.period.startswith(body.period)))
            totals = {}
            for row in rows:
                totals[row["metric"]] = totals.get(row["metric"], 0) + row["value"]
            lines = [{"metric": k, "quantity": totals.get(k, 0), "unit_microusd": v, "total_microusd": totals.get(k, 0) * v} for k, v in body.rates_microusd.items()]
            data = {"currency": "USD", "rates_microusd": body.rates_microusd, "lines": lines,
                    "total_microusd": sum(x["total_microusd"] for x in lines),
                    "note": "Usage statement only. No payment collected; taxes and contract terms are external."}
            item = dict(id=db.uid(), tenant_id=ident.tenant_id, period=body.period, data=data, status="draft", created_at=time.time())
            conn.execute(db.invoices.insert().values(**item))
            db.audit_event(conn, ident.tenant_id, ident.id, "invoice.created", item["id"])
            return item

    @app.get("/api/v1/billing/invoices")
    def list_invoices(ident: Identity = Depends(identity)):
        ident.require_org("billing")
        with database.tx(ident.tenant_id) as conn:
            return {"items": db.rows(conn, db.scoped(db.invoices, ident.tenant_id).order_by(db.invoices.c.created_at.desc()))}

    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    @app.get("/trust", include_in_schema=False)
    def trust():
        return FileResponse(static / "trust.html")

    return app
