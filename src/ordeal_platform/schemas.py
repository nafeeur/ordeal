from __future__ import annotations

from typing import Any, Literal
import math
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ProjectCreate(Model):
    name: str = Field(min_length=1, max_length=200)
    settings: dict = Field(default_factory=dict)


class ProjectSettings(Model):
    retention_days: int = Field(default=30, ge=1, le=36500)
    artifact_retention_days: int = Field(default=90, ge=1, le=36500)
    suppress_inputs: bool = False
    suppress_outputs: bool = False
    redact_pii: bool = True
    sensitive_fields: list[str] = Field(default_factory=list, max_length=50)
    limits: dict[str, int] = Field(default_factory=lambda: {"traces_daily": 100000, "bytes_daily": 1073741824, "jobs_daily": 10000, "cost_microusd_daily": 100000000})
    online_evaluators: list[dict] = Field(default_factory=list, max_length=20)
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("limits")
    @classmethod
    def positive_limits(cls, value):
        allowed = {"traces_daily", "bytes_daily", "jobs_daily", "cost_microusd_daily"}
        if set(value) - allowed or any(isinstance(v, bool) or v < 0 for v in value.values()):
            raise ValueError("Unknown or negative quota")
        return value

    @field_validator("online_evaluators")
    @classmethod
    def validate_online(cls, value):
        for item in value:
            if set(item) - {"id", "version", "sample_rate"} or not isinstance(item.get("id"), str):
                raise ValueError("Online evaluators require id, version, and sample_rate")
            if type(item.get("version")) is not int or item["version"] < 1:
                raise ValueError("Online evaluators must pin a version")
            if type(item.get("sample_rate", 1)) not in {float, int} or not math.isfinite(item.get("sample_rate", 1)) or not 0 <= item.get("sample_rate", 1) <= 1:
                raise ValueError("sample_rate must be in [0,1]")
        return value


class ResourceCreate(Model):
    kind: Literal["dataset", "world", "scenario", "evaluator", "agent", "prompt", "monitor", "connector", "automation", "experiment", "bundle", "incident", "review_queue"]
    name: str = Field(min_length=1, max_length=200)
    data: dict = Field(default_factory=dict)


class VersionCreate(Model):
    base_version: int = Field(ge=1)
    data: dict


class Promote(Model):
    environment: Literal["development", "staging", "production"]
    version: int = Field(ge=1)


class PrincipalCreate(Model):
    name: str = Field(min_length=1, max_length=200)
    username: str | None = Field(default=None, max_length=320)
    role: Literal["owner", "admin", "developer", "reviewer", "viewer", "billing", "runner"] = "viewer"
    kind: Literal["human", "service", "runner"] = "service"
    projects: list[str] = Field(default_factory=list, max_length=100)
    permissions: list[str] = Field(default_factory=list, max_length=20)
    token_days: int = Field(default=90, ge=1, le=365)


class PrincipalUpdate(Model):
    active: bool


class TokenCreate(Model):
    label: str = Field(default="api", min_length=1, max_length=200)
    days: int = Field(default=90, ge=1, le=365)


class SessionLogin(Model):
    token: str = Field(min_length=20, max_length=1024)


class TraceIn(Model):
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)
    name: str = Field(default="agent-run", min_length=1, max_length=200)
    environment: str = Field(default="development", min_length=1, max_length=40)
    agent_version: str | None = Field(default=None, max_length=120)
    status: Literal["pass", "fail", "error", "incomplete", "unset"] = "unset"
    duration_ms: float = Field(default=0, ge=0, le=1e12)
    usage: dict = Field(default_factory=dict)
    trajectory: dict = Field(default_factory=lambda: {"events": []})
    spans: list[dict] = Field(default_factory=list, max_length=10000)
    metadata: dict = Field(default_factory=dict)
    checks: list[dict] = Field(default_factory=list)


class JobCreate(Model):
    kind: Literal["suite", "evaluate"] = "suite"
    payload: dict
    labels: list[str] = Field(default_factory=list, max_length=20)
    priority: int = Field(default=0, ge=-100, le=100)
    max_attempts: int = Field(default=3, ge=1, le=10)
    estimated_cost_usd: float = Field(default=0, ge=0, le=1000000)
    not_before: float = Field(default=0, ge=0)
    idempotency_key: str | None = Field(default=None, max_length=128, min_length=1)


class RunnerRegister(Model):
    name: str = Field(min_length=1, max_length=200)
    labels: list[str] = Field(default_factory=list, max_length=20)


class Claim(Model):
    runner_id: str
    kinds: list[Literal["suite", "evaluate"]] = Field(default_factory=lambda: ["suite"])


class Lease(Model):
    lease_token: str = Field(min_length=10, max_length=100)
    runner_id: str


class Complete(Lease):
    result: dict
    actual_cost_usd: float = Field(default=0, ge=0, le=1000000)


class JobFailure(Lease):
    message: str = Field(max_length=2000)
    retryable: bool = True


class SecretCreate(Model):
    name: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    value: str = Field(min_length=1, max_length=65536)


class ReviewCreate(Model):
    trace_id: str
    queue: str = Field(default="default", min_length=1, max_length=128)
    assignee: str | None = None


class ReviewUpdate(Model):
    status: Literal["unreviewed", "in_review", "approved", "rejected", "escalated"]
    assignee: str | None = None
    labels: dict = Field(default_factory=dict)
    comment: str = Field(default="", max_length=10000)


class TraceToDataset(Model):
    dataset_id: str
    base_version: int = Field(ge=1)
    instruction: str = Field(default="", max_length=20000)
    expected: Any = None


class EvaluateRequest(Model):
    evaluator_id: str
    version: int = Field(ge=1)


class CachePut(Model):
    namespace: str = Field(min_length=1, max_length=100)
    key: dict
    value: Any
    ttl_seconds: int = Field(default=3600, ge=1, le=604800)


class InvoiceCreate(Model):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    # Integer micro-USD per metered unit; configured by the operator, no implied pricing.
    rates_microusd: dict[str, int]


class CompareRequest(Model):
    baseline: dict
    candidate: dict
    min_pass_rate: float = Field(default=0.95, ge=0, le=1)
    max_pass_rate_drop: float = Field(default=0, ge=0, le=1)


class PromptRender(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int | None = Field(None, ge=1)
    environment: Literal["development", "staging", "production"] | None = None
    variables: dict[str, str] = Field(default_factory=dict)
