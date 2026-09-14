from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import random
import math
import asyncio
from typing import Any, Mapping

from .models import Trajectory
from .tools import Tool, ToolContext, ToolMode

JSON = Any


class ToolNotFoundError(KeyError):
    pass


class RealToolBlockedError(RuntimeError):
    pass


@dataclass(slots=True)
class Fault:
    tool: str
    on_call: int = 1
    error: str = "simulated tool failure"
    kind: str = "error"
    delay_ms: float = 0.0
    result: JSON = None

    def __post_init__(self) -> None:
        if not isinstance(self.tool, str) or not self.tool.strip() or type(self.on_call) is not int or self.on_call < 1:
            raise ValueError("Faults require a tool and a positive integer call number")
        if self.kind not in {"error", "delay", "return", "timeout"}:
            raise ValueError("Unsupported fault kind")
        if not math.isfinite(self.delay_ms) or not 0 <= self.delay_ms <= 86400000:
            raise ValueError("Fault delay must be finite and between zero and one day")

    @classmethod
    def delay(cls, tool: str, *, on_call: int = 1, delay_ms: float = 100.0) -> "Fault":
        return cls(tool, on_call=on_call, error="", kind="delay", delay_ms=delay_ms)

    @classmethod
    def returning(cls, tool: str, result: JSON, *, on_call: int = 1) -> "Fault":
        return cls(tool, on_call=on_call, error="", kind="return", result=result)

    @classmethod
    def timeout(cls, tool: str, *, on_call: int = 1, message: str = "simulated timeout") -> "Fault":
        return cls(tool, on_call=on_call, error=message, kind="timeout")


@dataclass(slots=True)
class World:
    name: str
    initial_state: Mapping[str, JSON] = field(default_factory=dict)
    tools: list[Tool] = field(default_factory=list)
    allow_passthrough: bool = False
    seed: int = 0

    def __post_init__(self) -> None:
        names = [tool.name for tool in self.tools]
        if len(set(names)) != len(names):
            raise ValueError("World tool names must be unique")

    def spawn(self, scenario_name: str, *, seed_offset: int = 0, faults: list[Fault] | None = None) -> "WorldRun":
        return WorldRun(
            definition=self,
            scenario_name=scenario_name,
            state=deepcopy(dict(self.initial_state)),
            rng=random.Random(self.seed + seed_offset),
            faults=faults or [],
        )


@dataclass(slots=True)
class WorldRun:
    definition: World
    scenario_name: str
    state: dict[str, JSON]
    rng: random.Random
    faults: list[Fault] = field(default_factory=list)
    trajectory: Trajectory = field(default_factory=Trajectory)
    _tool_counts: dict[str, int] = field(default_factory=dict)
    replay_cursors: dict[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.trajectory.initial_state = deepcopy(self.state)

    @property
    def tool_map(self) -> dict[str, Tool]:
        return {tool.name: tool for tool in self.definition.tools}

    def get(self, key: str, default: JSON = None) -> JSON:
        return self.state.get(key, default)

    def set(self, key: str, value: JSON) -> None:
        before = deepcopy(self.state.get(key))
        self.state[key] = deepcopy(value)
        self.trajectory.add("state_change", key, before=before, after=deepcopy(value))

    def patch(self, values: Mapping[str, JSON]) -> None:
        for key, value in values.items():
            self.set(key, value)

    async def call_tool(self, name: str, arguments: Mapping[str, JSON]) -> JSON:
        tool = self.tool_map.get(name)
        if tool is None:
            self.trajectory.add("error", "tool_not_found", tool=name)
            raise ToolNotFoundError(name)
        if tool.mode is ToolMode.PASSTHROUGH and not self.definition.allow_passthrough:
            self.trajectory.add("error", "passthrough_blocked", tool=name)
            raise RealToolBlockedError(
                f"passthrough tool {name!r} is blocked; explicitly set allow_passthrough=True"
            )

        call_index = self._tool_counts.get(name, 0) + 1
        self._tool_counts[name] = call_index
        self.trajectory.add("tool_call", name, arguments=deepcopy(dict(arguments)), call_index=call_index)

        for fault in self.faults:
            if fault.tool != name or fault.on_call != call_index:
                continue
            self.trajectory.add(
                "fault",
                fault.kind,
                tool=name,
                call_index=call_index,
                delay_ms=fault.delay_ms,
                message=fault.error,
            )
            if fault.kind == "delay":
                await asyncio.sleep(max(0.0, fault.delay_ms) / 1000.0)
                continue
            if fault.kind == "return":
                output = deepcopy(fault.result)
                self.trajectory.add("tool_result", name, result=deepcopy(output), call_index=call_index, injected=True)
                return output
            if fault.kind == "timeout":
                self.trajectory.add("error", "tool_timeout", tool=name, message=fault.error)
                raise TimeoutError(fault.error)
            if fault.kind != "error":
                raise ValueError(f"unknown fault kind {fault.kind!r}")
            self.trajectory.add("error", "tool_fault", tool=name, message=fault.error)
            raise RuntimeError(fault.error)

        context = ToolContext(world=self, scenario_name=self.scenario_name, call_index=call_index)
        output = await tool.invoke(arguments, context)
        self.trajectory.add("tool_result", name, result=deepcopy(output), call_index=call_index)
        return deepcopy(output)

    def finish(self, output: JSON) -> Trajectory:
        self.trajectory.final_output = deepcopy(output)
        self.trajectory.final_state = deepcopy(self.state)
        return self.trajectory
