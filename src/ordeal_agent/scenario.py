from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
import math
from typing import Any, Iterable, Mapping, Sequence

from .assertions import Assertion
from .world import Fault, World

JSON = Any


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    variables: Mapping[str, JSON] = field(default_factory=dict)
    state: Mapping[str, JSON] = field(default_factory=dict)
    metadata: Mapping[str, JSON] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Budget:
    max_total_tokens: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    max_cost_usd: float | None = None
    max_latency_ms: float | None = None

    def __post_init__(self):
        for name in ("max_total_tokens", "max_input_tokens", "max_output_tokens", "max_cost_usd", "max_latency_ms"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError("Budget limits must be finite nonnegative numbers")


@dataclass(slots=True)
class Scenario:
    name: str
    instruction: str
    world: World
    assertions: list[Assertion] = field(default_factory=list)
    repetitions: int = 1
    faults: list[Fault] = field(default_factory=list)
    metadata: Mapping[str, JSON] = field(default_factory=dict)
    cases: list[Case] = field(default_factory=lambda: [Case("default")])
    limits: Budget = field(default_factory=Budget)

    def expect(self, *assertions: Assertion) -> "Scenario":
        self.assertions.extend(assertions)
        return self

    def repeat(self, count: int) -> "Scenario":
        if count < 1:
            raise ValueError("repetition count must be >= 1")
        self.repetitions = count
        return self

    def with_cases(self, *cases: Case) -> "Scenario":
        if not cases:
            raise ValueError("at least one case is required")
        ids = [case.id for case in cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        self.cases = list(cases)
        return self

    def parameterize(self, rows: Sequence[Mapping[str, JSON]], *, id_key: str = "id") -> "Scenario":
        built: list[Case] = []
        for index, row in enumerate(rows):
            values = dict(row)
            case_id = str(values.pop(id_key, index))
            built.append(Case(case_id, variables=values))
        return self.with_cases(*built)

    def budget(
        self,
        *,
        max_total_tokens: int | None = None,
        max_input_tokens: int | None = None,
        max_output_tokens: int | None = None,
        max_cost_usd: float | None = None,
        max_latency_ms: float | None = None,
    ) -> "Scenario":
        self.limits = Budget(
            max_total_tokens=max_total_tokens,
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            max_cost_usd=max_cost_usd,
            max_latency_ms=max_latency_ms,
        )
        return self

    def resolved(self, case: Case) -> tuple[str, World, Mapping[str, JSON]]:
        try:
            instruction = self.instruction.format_map(dict(case.variables))
        except KeyError as exc:
            raise ValueError(f"missing scenario variable {exc.args[0]!r} for case {case.id!r}") from exc
        world = replace(self.world, initial_state=deepcopy(dict(self.world.initial_state) | dict(case.state)))
        metadata = dict(self.metadata) | dict(case.metadata) | {"case_id": case.id, "variables": dict(case.variables)}
        return instruction, world, metadata


@dataclass(slots=True)
class Suite:
    name: str
    scenarios: list[Scenario]
    concurrency: int = 4

    @classmethod
    def of(cls, name: str, *scenarios: Scenario, concurrency: int = 4) -> "Suite":
        return cls(name=name, scenarios=list(scenarios), concurrency=concurrency)

    def extend(self, scenarios: Iterable[Scenario]) -> None:
        self.scenarios.extend(scenarios)
