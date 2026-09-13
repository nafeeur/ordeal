from typing import Any, Literal
from pydantic import BaseModel, Field

class AgentSpec(BaseModel):
    name: str
    version: str = "dev"
    dispatch_type: Literal["mock", "http", "openai_compatible"] = "mock"
    endpoint: str | None = None
    model: str | None = None
    api_key_env: str | None = None
    system_prompt: str | None = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    behavior: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

class WorldSpec(BaseModel):
    name: str
    state: dict[str, Any] = Field(default_factory=dict)
    schema_: dict[str, Any] = Field(default_factory=dict, alias="schema")
    metadata: dict[str, Any] = Field(default_factory=dict)
    model_config = {"populate_by_name": True}

class ScenarioSpec(BaseModel):
    name: str
    world: str
    instruction: str
    variables: dict[str, Any] = Field(default_factory=dict)
    setup: list[dict[str, Any]] = Field(default_factory=list)
    faults: list[dict[str, Any]] = Field(default_factory=list)
    assertions: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    repetitions: int = 1
    timeout_seconds: int = 120

class SuiteSpec(BaseModel):
    name: str
    scenarios: list[str]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConstraintSpec(BaseModel):
    name: str
    description: str = ""
    version: str = "v1"
    severity: Literal["info","minor","major","critical"] = "major"
    assertion: dict[str, Any]
    bindings: dict[str, list[str]] = Field(default_factory=dict)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

class DatasetSpec(BaseModel):
    name: str
    rows: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

class RunRequest(BaseModel):
    name: str
    agent: str
    suite: str
    seed: int = 1
    distributed: bool = False
    repetitions: int = 1
    concurrency: int = 16
    baseline_run_id: int | None = None
    commit_sha: str | None = None
    base_commit_sha: str | None = None
    gate_on_new_regressions_only: bool = True

class CompareRequest(BaseModel):
    baseline_run_id: int
    candidate_run_id: int

class ReplayRequest(BaseModel):
    run_id: int
    scenario: str
    seed: int | None = None

class CompileWorldRequest(BaseModel):
    name: str = "compiled-world"
    openapi: dict[str, Any] = Field(default_factory=dict)
    mcp_tools: list[dict[str, Any]] = Field(default_factory=list)
    traces: list[dict[str, Any]] = Field(default_factory=list)
    persist: bool = True

class FuzzRequest(BaseModel):
    agent: str
    scenario: str
    iterations: int = 40
    seed: int = 1
    fault_tools: list[str] = Field(default_factory=list)
    coverage_guided: bool = True

class ShrinkRequest(BaseModel):
    agent: str
    scenario: dict[str, Any]
    world: dict[str, Any]
    seed: int = 1

class IncidentRequest(BaseModel):
    name: str
    agent: str
    world: str
    instruction: str
    variables: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    expected_assertions: list[dict[str, Any]] = Field(default_factory=list)

class LearnSimulatorRequest(BaseModel):
    name: str
    traces: list[dict[str, Any]] = Field(default_factory=list)
    persist: bool = True

class ConformanceRequest(BaseModel):
    profile: str
    traces: list[dict[str, Any]] = Field(default_factory=list)

class CausalRequest(BaseModel):
    run_id: int
    scenario: str
    passing_run_id: int | None = None

class WorkerRegisterRequest(BaseModel):
    worker_id: str
    labels: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=lambda:["cpu"])
    capacity: int = 1

class WorkerHeartbeatRequest(BaseModel):
    worker_id: str
    active_jobs: int | None = None

class JobClaimRequest(BaseModel):
    worker_id: str
    capabilities: list[str] = Field(default_factory=lambda:["cpu"])

class JobCompleteRequest(BaseModel):
    worker_id: str
    lease_token: str
    result: dict[str, Any] = Field(default_factory=dict)
    failed: bool = False

class ApiKeyCreateRequest(BaseModel):
    name: str
    role: Literal["viewer","operator","admin"] = "operator"
    scopes: list[str] = Field(default_factory=lambda:["*"])

class MatrixRequest(BaseModel):
    name: str
    agents: list[str]
    suite: str
    seeds: list[int] = Field(default_factory=lambda:[1])
    repetitions: int = 1
    distributed: bool = True
    capability: str = "cpu"


class RuntimeVerificationRequest(BaseModel):
    execution: str = "runtime-execution"
    contract: dict[str, Any]
    initial_state: dict[str, Any] = Field(default_factory=dict)
    final_state: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    adapters: dict[str, str] = Field(default_factory=dict)
    expected_chain_head: str | None = None

class AdapterConformanceRequest(BaseModel):
    kind: Literal["target","state","runtime","fault","verifier"]
    name: str
    required_capabilities: list[str] = Field(default_factory=list)
