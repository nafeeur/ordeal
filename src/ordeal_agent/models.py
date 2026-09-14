from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
import hashlib
import json
import math
import time
from collections import Counter, defaultdict
from statistics import median
from typing import Any, Mapping

JSON = Any


def canonical_json(value: JSON) -> str:
    def default(obj: Any) -> Any:
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, Enum):
            return obj.value
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        raise TypeError(f"not JSON serializable: {type(obj)!r}")

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=default, allow_nan=False)


def sha256_json(value: JSON) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INCOMPLETE = "incomplete"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    model: str | None = None
    measured: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_mapping(cls, value: Mapping[str, JSON] | None) -> "Usage":
        data = dict(value or {})
        aliases = {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens", "cost_usd": "cost"}
        measured = set()
        values = {}
        for key in ("input_tokens", "output_tokens", "total_tokens", "cost_usd"):
            raw = data.get(key, data.get(aliases.get(key, key)))
            if raw is None:
                values[key] = 0
                continue
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(raw) or raw < 0:
                raise ValueError("Usage measurements must be finite, nonnegative numbers")
            if key != "cost_usd" and int(raw) != raw:
                raise ValueError("Token counts must be integers")
            values[key] = float(raw) if key == "cost_usd" else int(raw)
            measured.add(key)
        if "total_tokens" not in measured and {"input_tokens", "output_tokens"}.issubset(measured):
            values["total_tokens"] = values["input_tokens"] + values["output_tokens"]
            measured.add("total_tokens")
        return cls(**values, model=str(data["model"]) if data.get("model") is not None else None,
                   measured=frozenset(measured))

    def to_dict(self) -> dict[str, JSON]:
        # Unknown usage is null, never a fabricated zero-cost result.
        return {**{key: getattr(self, key) if key in self.measured else None
                   for key in ("input_tokens", "output_tokens", "total_tokens", "cost_usd")},
                "model": self.model}


@dataclass(frozen=True, slots=True)
class Event:
    seq: int
    kind: str
    name: str
    payload: Mapping[str, JSON] = field(default_factory=dict)
    time_ns: int = field(default_factory=time.time_ns)

    def structural(self) -> dict[str, JSON]:
        payload: dict[str, JSON] = {}
        if self.kind == "tool_call":
            args = self.payload.get("arguments", {})
            payload["argument_keys"] = sorted(args) if isinstance(args, dict) else []
        elif self.kind in {"state_change", "tool_result", "error", "model_call", "model_result"}:
            payload["keys"] = sorted(self.payload)
        return {"kind": self.kind, "name": self.name, "payload": payload}


@dataclass(slots=True)
class Trajectory:
    events: list[Event] = field(default_factory=list)
    final_output: JSON = None
    initial_state: Mapping[str, JSON] = field(default_factory=dict)
    final_state: Mapping[str, JSON] = field(default_factory=dict)

    def add(self, kind: str, name: str, **payload: JSON) -> Event:
        event = Event(seq=len(self.events), kind=kind, name=name, payload=payload)
        self.events.append(event)
        return event

    @property
    def structural_fingerprint(self) -> str:
        return sha256_json([event.structural() for event in self.events])

    @property
    def content_fingerprint(self) -> str:
        return sha256_json(
            {
                "events": [
                    {"seq": e.seq, "kind": e.kind, "name": e.name, "payload": e.payload}
                    for e in self.events
                ],
                "final_output": self.final_output,
                "final_state": self.final_state,
            }
        )

    def tool_names(self) -> list[str]:
        return [e.name for e in self.events if e.kind == "tool_call"]


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    verdict: Verdict
    message: str = ""
    evidence: Mapping[str, JSON] = field(default_factory=dict)


@dataclass(slots=True)
class ScenarioResult:
    scenario: str
    repetition: int
    verdict: Verdict
    checks: list[CheckResult]
    trajectory: Trajectory
    duration_ms: float
    error: str | None = None
    case_id: str = "default"
    usage: Usage = field(default_factory=Usage)

    @property
    def result_id(self) -> str:
        return f"{self.scenario}:{self.case_id}:{self.repetition}"

    def to_dict(self) -> dict[str, JSON]:
        return {
            "scenario": self.scenario,
            "case_id": self.case_id,
            "repetition": self.repetition,
            "verdict": self.verdict.value,
            "checks": [asdict(c) | {"verdict": c.verdict.value} for c in self.checks],
            "trajectory": {
                "events": [asdict(e) for e in self.trajectory.events],
                "final_output": self.trajectory.final_output,
                "initial_state": self.trajectory.initial_state,
                "final_state": self.trajectory.final_state,
                "structural_fingerprint": self.trajectory.structural_fingerprint,
                "content_fingerprint": self.trajectory.content_fingerprint,
            },
            "duration_ms": self.duration_ms,
            "usage": self.usage.to_dict(),
            "error": self.error,
        }


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


@dataclass(slots=True)
class SuiteReport:
    suite: str
    agent_name: str
    agent_version: str
    results: list[ScenarioResult]
    started_at: float
    finished_at: float

    @property
    def counts(self) -> dict[str, int]:
        out = {v.value: 0 for v in Verdict}
        for result in self.results:
            out[result.verdict.value] += 1
        return out

    @property
    def pass_rate(self) -> float:
        if not self.results:
            return 0.0
        return self.counts[Verdict.PASS.value] / len(self.results)

    @property
    def usage(self) -> Usage:
        return Usage(
            input_tokens=sum(r.usage.input_tokens for r in self.results),
            output_tokens=sum(r.usage.output_tokens for r in self.results),
            total_tokens=sum(r.usage.total_tokens for r in self.results),
            cost_usd=sum(r.usage.cost_usd for r in self.results),
            measured=frozenset.intersection(*(r.usage.measured for r in self.results)) if self.results else frozenset(),
        )

    @property
    def latency(self) -> dict[str, float]:
        values = [r.duration_ms for r in self.results]
        return {
            "min_ms": min(values) if values else 0.0,
            "p50_ms": median(values) if values else 0.0,
            "p95_ms": _percentile(values, 0.95),
            "p99_ms": _percentile(values, 0.99),
            "max_ms": max(values) if values else 0.0,
        }

    @property
    def flakiness(self) -> dict[str, dict[str, JSON]]:
        groups: dict[tuple[str, str], list[ScenarioResult]] = defaultdict(list)
        for result in self.results:
            groups[(result.scenario, result.case_id)].append(result)
        output: dict[str, dict[str, JSON]] = {}
        for (scenario, case_id), rows in groups.items():
            verdicts = Counter(r.verdict.value for r in rows)
            structures = Counter(r.trajectory.structural_fingerprint for r in rows)
            max_verdict_share = max(verdicts.values()) / len(rows)
            max_structure_share = max(structures.values()) / len(rows)
            flaky = len(rows) > 1 and (len(verdicts) > 1 or len(structures) > 1)
            output[f"{scenario}:{case_id}"] = {
                "runs": len(rows),
                "flaky": flaky,
                "verdict_consistency": max_verdict_share,
                "structure_consistency": max_structure_share,
                "verdicts": dict(verdicts),
                "unique_structures": len(structures),
            }
        return output

    @property
    def flaky_rate(self) -> float:
        values = list(self.flakiness.values())
        if not values:
            return 0.0
        return sum(bool(v["flaky"]) for v in values) / len(values)

    def to_dict(self) -> dict[str, JSON]:
        return {
            "schema": "ordeal.behavior-report/v2",
            "suite": self.suite,
            "agent": {"name": self.agent_name, "version": self.agent_version},
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pass_rate": self.pass_rate,
            "counts": self.counts,
            "usage": self.usage.to_dict(),
            "latency": self.latency,
            "flaky_rate": self.flaky_rate,
            "flakiness": self.flakiness,
            "results": [r.to_dict() for r in self.results],
        }
