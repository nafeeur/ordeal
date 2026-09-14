from __future__ import annotations

import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.pool import StaticPool

metadata = sa.MetaData()
SCHEMA_VERSION = 1


def table(name: str, *columns, **kw):
    return sa.Table(name, metadata, *columns, **kw)


def idcol():
    return sa.Column("id", sa.String(64), primary_key=True)


def tenantcol():
    return sa.Column("tenant_id", sa.String(64), nullable=False, index=True)


def createdcol():
    return sa.Column("created_at", sa.Float, nullable=False)


schema_versions = table("schema_versions", sa.Column("version", sa.Integer, primary_key=True), createdcol())
tenants = table("tenants", idcol(), sa.Column("name", sa.String(200), nullable=False), createdcol())
projects = table("projects", idcol(), tenantcol(), sa.Column("name", sa.String(200), nullable=False),
                 sa.Column("settings", sa.JSON, nullable=False), createdcol(),
                 sa.UniqueConstraint("tenant_id", "name"))
principals = table("principals", idcol(), tenantcol(), sa.Column("name", sa.String(200), nullable=False),
                   sa.Column("username", sa.String(320)), sa.Column("external_id", sa.String(320)),
                   sa.Column("role", sa.String(32), nullable=False), sa.Column("kind", sa.String(32), nullable=False),
                   sa.Column("projects", sa.JSON, nullable=False), sa.Column("permissions", sa.JSON, nullable=False),
                   sa.Column("active", sa.Boolean, nullable=False), createdcol(),
                   sa.UniqueConstraint("tenant_id", "username"))
tokens = table("tokens", idcol(), tenantcol(), sa.Column("principal_id", sa.String(64), nullable=False),
               sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
               sa.Column("label", sa.String(200), nullable=False), sa.Column("expires_at", sa.Float, nullable=False),
               sa.Column("revoked", sa.Boolean, nullable=False), createdcol())
groups = table("groups", idcol(), tenantcol(), sa.Column("name", sa.String(200), nullable=False),
               sa.Column("members", sa.JSON, nullable=False), sa.Column("role", sa.String(32)), createdcol())
resources = table("resources", idcol(), tenantcol(), sa.Column("project_id", sa.String(64), nullable=False, index=True),
                  sa.Column("kind", sa.String(40), nullable=False), sa.Column("name", sa.String(200), nullable=False),
                  sa.Column("head", sa.Integer, nullable=False), sa.Column("aliases", sa.JSON, nullable=False),
                  sa.Column("deleted", sa.Boolean, nullable=False), createdcol(),
                  sa.UniqueConstraint("tenant_id", "project_id", "kind", "name"))
versions = table("versions", idcol(), tenantcol(), sa.Column("resource_id", sa.String(64), nullable=False, index=True),
                 sa.Column("version", sa.Integer, nullable=False), sa.Column("data", sa.JSON, nullable=False),
                 sa.Column("content_hash", sa.String(64), nullable=False), sa.Column("author", sa.String(64), nullable=False),
                 createdcol(), sa.UniqueConstraint("resource_id", "version"))
traces = table("traces", idcol(), tenantcol(), sa.Column("project_id", sa.String(64), nullable=False, index=True),
               sa.Column("trace_id", sa.String(128), nullable=False), sa.Column("name", sa.String(200), nullable=False),
               sa.Column("environment", sa.String(40), nullable=False), sa.Column("agent_version", sa.String(120)),
               sa.Column("status", sa.String(32), nullable=False), sa.Column("duration_ms", sa.Float, nullable=False),
               sa.Column("cost_usd", sa.Float), sa.Column("total_tokens", sa.BigInteger),
               sa.Column("data", sa.JSON, nullable=False), sa.Column("content_hash", sa.String(64), nullable=False),
               sa.Column("legal_hold", sa.Boolean, nullable=False), createdcol(),
               sa.UniqueConstraint("tenant_id", "project_id", "trace_id"))
evaluations = table("evaluations", idcol(), tenantcol(), sa.Column("project_id", sa.String(64), nullable=False),
                    sa.Column("trace_id", sa.String(64), nullable=False, index=True),
                    sa.Column("evaluator_id", sa.String(64)), sa.Column("evaluator_version", sa.Integer),
                    sa.Column("result", sa.JSON, nullable=False), createdcol())
reviews = table("reviews", idcol(), tenantcol(), sa.Column("project_id", sa.String(64), nullable=False),
                sa.Column("trace_id", sa.String(64), nullable=False), sa.Column("queue", sa.String(128), nullable=False),
                sa.Column("assignee", sa.String(64)), sa.Column("status", sa.String(32), nullable=False),
                sa.Column("labels", sa.JSON, nullable=False), sa.Column("comment", sa.Text, nullable=False), createdcol())
jobs = table("jobs", idcol(), tenantcol(), sa.Column("project_id", sa.String(64), nullable=False, index=True),
             sa.Column("kind", sa.String(32), nullable=False), sa.Column("payload", sa.JSON, nullable=False),
             sa.Column("labels", sa.JSON, nullable=False), sa.Column("state", sa.String(32), nullable=False, index=True),
             sa.Column("priority", sa.Integer, nullable=False), sa.Column("attempt", sa.Integer, nullable=False),
             sa.Column("max_attempts", sa.Integer, nullable=False), sa.Column("not_before", sa.Float, nullable=False),
             sa.Column("lease_until", sa.Float), sa.Column("lease_token", sa.String(80)),
             sa.Column("worker", sa.String(64)), sa.Column("result", sa.JSON), sa.Column("error", sa.Text),
             sa.Column("estimated_microusd", sa.BigInteger, nullable=False), sa.Column("actual_microusd", sa.BigInteger),
             sa.Column("idempotency_key", sa.String(128)), createdcol(),
             sa.UniqueConstraint("tenant_id", "project_id", "idempotency_key"))
runners = table("runners", idcol(), tenantcol(), sa.Column("principal_id", sa.String(64), nullable=False),
                sa.Column("name", sa.String(200), nullable=False), sa.Column("labels", sa.JSON, nullable=False),
                sa.Column("last_seen", sa.Float, nullable=False), createdcol())
secrets = table("secrets", idcol(), tenantcol(), sa.Column("project_id", sa.String(64), nullable=False),
                sa.Column("name", sa.String(200), nullable=False), sa.Column("ciphertext", sa.Text, nullable=False),
                sa.Column("key_id", sa.String(128), nullable=False), createdcol(),
                sa.UniqueConstraint("tenant_id", "project_id", "name"))
artifacts = table("artifacts", idcol(), tenantcol(), sa.Column("project_id", sa.String(64), nullable=False),
                  sa.Column("sha256", sa.String(64), nullable=False), sa.Column("size", sa.BigInteger, nullable=False),
                  sa.Column("media_type", sa.String(128), nullable=False), sa.Column("path", sa.String(512), nullable=False),
                  sa.Column("key_id", sa.String(128), nullable=False), sa.Column("legal_hold", sa.Boolean, nullable=False), createdcol())
audit = table("audit", idcol(), tenantcol(), sa.Column("sequence", sa.BigInteger, nullable=False),
              sa.Column("actor", sa.String(64), nullable=False), sa.Column("action", sa.String(100), nullable=False),
              sa.Column("resource_id", sa.String(128)), sa.Column("details", sa.JSON, nullable=False),
              sa.Column("previous_hash", sa.String(64), nullable=False), sa.Column("hash", sa.String(64), nullable=False),
              createdcol(), sa.UniqueConstraint("tenant_id", "sequence"))
usage = table("usage", idcol(), tenantcol(), sa.Column("project_id", sa.String(64), nullable=False),
              sa.Column("period", sa.String(10), nullable=False), sa.Column("metric", sa.String(80), nullable=False),
              sa.Column("value", sa.BigInteger, nullable=False), createdcol(),
              sa.UniqueConstraint("tenant_id", "project_id", "period", "metric"))
deliveries = table("deliveries", idcol(), tenantcol(), sa.Column("project_id", sa.String(64), nullable=False),
                   sa.Column("connector_id", sa.String(64), nullable=False), sa.Column("connector_version", sa.Integer, nullable=False),
                   sa.Column("event_id", sa.String(64), nullable=False), sa.Column("event", sa.String(100), nullable=False),
                   sa.Column("payload", sa.JSON, nullable=False), sa.Column("state", sa.String(32), nullable=False),
                   sa.Column("attempt", sa.Integer, nullable=False), sa.Column("not_before", sa.Float, nullable=False),
                   sa.Column("lease_token", sa.String(80)), sa.Column("lease_until", sa.Float),
                   sa.Column("last_error", sa.Text), createdcol())
alerts = table("alerts", idcol(), tenantcol(), sa.Column("project_id", sa.String(64), nullable=False),
               sa.Column("monitor_id", sa.String(64), nullable=False), sa.Column("state", sa.String(32), nullable=False),
               sa.Column("data", sa.JSON, nullable=False), createdcol())
cache = table("cache", idcol(), tenantcol(), sa.Column("project_id", sa.String(64), nullable=False),
              sa.Column("key", sa.String(64), nullable=False), sa.Column("data", sa.JSON, nullable=False),
              sa.Column("expires_at", sa.Float, nullable=False), createdcol(),
              sa.UniqueConstraint("tenant_id", "project_id", "key"))
login_states = table("login_states", idcol(), sa.Column("data", sa.JSON, nullable=False),
                     sa.Column("expires_at", sa.Float, nullable=False))
invoices = table("invoices", idcol(), tenantcol(), sa.Column("period", sa.String(7), nullable=False),
                 sa.Column("data", sa.JSON, nullable=False), sa.Column("status", sa.String(32), nullable=False),
                 createdcol(), sa.UniqueConstraint("tenant_id", "period"))


def uid() -> str:
    return uuid.uuid4().hex


def canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(data: Any) -> str:
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def row(conn, query):
    item = conn.execute(query).mappings().first()
    return dict(item) if item else None


def rows(conn, query):
    return [dict(x) for x in conn.execute(query).mappings()]


def scoped(tbl, tenant_id: str):
    return sa.select(tbl).where(tbl.c.tenant_id == tenant_id)


class Database:
    def __init__(self, url: str):
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in url:
                kwargs["poolclass"] = StaticPool
            elif url.startswith("sqlite:///"):
                Path(url[len("sqlite:///"):]).parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.engine = sa.create_engine(url, **kwargs)
        self.sqlite = self.engine.dialect.name == "sqlite"
        if self.sqlite:
            @sa.event.listens_for(self.engine, "connect")
            def pragmas(dbapi_conn, _):
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()

    @contextmanager
    def tx(self, tenant_id: str | None = None):
        with self.engine.connect() as conn:
            if self.sqlite:
                # Serialize writes and quota reservations across local API processes.
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                conn.begin()
                if tenant_id:
                    conn.execute(sa.text("SELECT set_config('ordeal.tenant_id', :tenant, true)"), {"tenant": tenant_id})
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def migrate(self):
        # Initial schema is frozen in the bundled Alembic migration. Always upgrade through Alembic.
        from alembic import command
        from alembic.config import Config
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.engine.begin() as conn:
            config.attributes["connection"] = conn
            command.upgrade(config, "head")

    def check(self):
        with self.engine.connect() as conn:
            version = conn.execute(sa.select(sa.func.max(schema_versions.c.version))).scalar()
        if version != SCHEMA_VERSION:
            raise RuntimeError("Database schema mismatch; run ordeal-server migrate")


def audit_event(conn, tenant_id: str, actor: str, action: str, resource_id: str | None = None, details: dict | None = None):
    # Lock a per-tenant row before appending so concurrent PostgreSQL transactions cannot fork the chain.
    conn.execute(sa.select(tenants.c.id).where(tenants.c.id == tenant_id).with_for_update())
    prev = row(conn, scoped(audit, tenant_id).order_by(audit.c.sequence.desc()).limit(1))
    entry = dict(id=uid(), tenant_id=tenant_id, sequence=prev["sequence"] + 1 if prev else 1,
                 actor=actor, action=action, resource_id=resource_id, details=details or {},
                 previous_hash=prev["hash"] if prev else "0" * 64, created_at=time.time())
    entry["hash"] = digest(entry)
    conn.execute(audit.insert().values(**entry))
    return entry


def verify_audit(entries: list[dict]) -> dict:
    previous = "0" * 64
    for seq, entry in enumerate(entries, 1):
        plain = {k: v for k, v in entry.items() if k != "hash"}
        if entry["sequence"] != seq or entry["previous_hash"] != previous or digest(plain) != entry["hash"]:
            return {"valid": False, "first_invalid_sequence": entry["sequence"]}
        previous = entry["hash"]
    return {"valid": True, "entries": len(entries), "head": previous}
