from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Awaitable, Callable, Generic, Sequence, TypeVar

from .agents import Agent
from .models import ScenarioResult, Verdict
from .runner import Runner
from .scenario import Scenario

T = TypeVar("T")


async def ddmin(items: Sequence[T], fails: Callable[[list[T]], Awaitable[bool]]) -> list[T]:
    """Classic delta-debugging minimizer: return a 1-minimal failing subset."""
    current = list(items)
    if not current or not await fails(current):
        return current
    n = 2
    while len(current) >= 2:
        size = max(1, (len(current) + n - 1) // n)
        reduced = False
        for start in range(0, len(current), size):
            complement = current[:start] + current[start + size :]
            if complement and await fails(complement):
                current = complement
                n = max(2, n - 1)
                reduced = True
                break
        if reduced:
            continue
        if n >= len(current):
            break
        n = min(len(current), n * 2)
    return current


@dataclass(slots=True)
class ShrinkResult(Generic[T]):
    original: list[T]
    minimal: list[T]
    result: ScenarioResult


async def shrink_faults(agent: Agent, scenario: Scenario, *, runner: Runner | None = None) -> ShrinkResult:
    """Minimize a failing scenario's injected fault set while preserving non-PASS behavior."""
    runner = runner or Runner()
    original = list(scenario.faults)
    if not original:
        result = await runner.run_scenario(agent, scenario)
        return ShrinkResult([], [], result)

    async def fails(candidate: list) -> bool:
        trial = deepcopy(scenario)
        trial.faults = list(candidate)
        result = await runner.run_scenario(agent, trial)
        return result.verdict != Verdict.PASS

    minimal = await ddmin(original, fails)
    final_scenario = deepcopy(scenario)
    final_scenario.faults = minimal
    result = await runner.run_scenario(agent, final_scenario)
    return ShrinkResult(original, minimal, result)
