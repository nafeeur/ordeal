from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol
import inspect

from .models import CheckResult, Trajectory, Verdict

JSON = Any


class Assertion(Protocol):
    name: str
    async def evaluate(self, trajectory: Trajectory) -> CheckResult: ...


@dataclass(frozen=True, slots=True)
class OutputContains:
    text: str
    name: str = "output_contains"

    async def evaluate(self, trajectory: Trajectory) -> CheckResult:
        ok = self.text in str(trajectory.final_output)
        return CheckResult(self.name, Verdict.PASS if ok else Verdict.FAIL, f"expected output to contain {self.text!r}")


@dataclass(frozen=True, slots=True)
class ToolCalled:
    tool: str
    min_times: int = 1
    max_times: int | None = None
    name: str = "tool_called"

    async def evaluate(self, trajectory: Trajectory) -> CheckResult:
        count = trajectory.tool_names().count(self.tool)
        ok = count >= self.min_times and (self.max_times is None or count <= self.max_times)
        return CheckResult(
            self.name,
            Verdict.PASS if ok else Verdict.FAIL,
            f"{self.tool} called {count} times; expected {self.min_times}..{self.max_times or 'inf'}",
            {"tool": self.tool, "count": count},
        )


@dataclass(frozen=True, slots=True)
class ToolNotCalled:
    tool: str
    name: str = "tool_not_called"

    async def evaluate(self, trajectory: Trajectory) -> CheckResult:
        count = trajectory.tool_names().count(self.tool)
        return CheckResult(
            self.name,
            Verdict.PASS if count == 0 else Verdict.FAIL,
            f"{self.tool} called {count} times",
            {"tool": self.tool, "count": count},
        )


@dataclass(frozen=True, slots=True)
class ToolOrder:
    before: str
    after: str
    name: str = "tool_order"

    async def evaluate(self, trajectory: Trajectory) -> CheckResult:
        names = trajectory.tool_names()
        if self.before not in names or self.after not in names:
            return CheckResult(
                self.name,
                Verdict.INCOMPLETE,
                f"cannot establish order; observed tools: {names}",
                {"tools": names},
            )
        ok = names.index(self.before) < names.index(self.after)
        return CheckResult(
            self.name,
            Verdict.PASS if ok else Verdict.FAIL,
            f"expected {self.before} before {self.after}; observed {names}",
            {"tools": names},
        )


@dataclass(frozen=True, slots=True)
class StateEquals:
    key: str
    expected: JSON
    name: str = "state_equals"

    async def evaluate(self, trajectory: Trajectory) -> CheckResult:
        if self.key not in trajectory.final_state:
            return CheckResult(self.name, Verdict.INCOMPLETE, f"state key {self.key!r} is missing")
        actual = trajectory.final_state[self.key]
        ok = actual == self.expected
        return CheckResult(
            self.name,
            Verdict.PASS if ok else Verdict.FAIL,
            f"state[{self.key!r}]={actual!r}; expected {self.expected!r}",
            {"key": self.key, "actual": actual, "expected": self.expected},
        )


Predicate = Callable[[Trajectory], bool | CheckResult | Awaitable[bool | CheckResult]]


@dataclass(frozen=True, slots=True)
class CustomAssertion:
    name: str
    predicate: Predicate
    failure_message: str = "custom assertion failed"

    async def evaluate(self, trajectory: Trajectory) -> CheckResult:
        value = self.predicate(trajectory)
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, CheckResult):
            return value
        return CheckResult(self.name, Verdict.PASS if value else Verdict.FAIL, "" if value else self.failure_message)
