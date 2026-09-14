"""Initial standalone platform schema; frozen snapshot."""
import time
import sqlalchemy as sa
from alembic import op
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

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


def upgrade():
    bind = op.get_bind()
    metadata.create_all(bind)
    bind.execute(schema_versions.insert().values(version=1, created_at=time.time()))

def downgrade():
    raise RuntimeError("Destructive downgrade disabled. Restore a verified backup instead.")
