from __future__ import annotations

from dataclasses import asdict, dataclass, field
from copy import deepcopy
import inspect
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from .models import canonical_json
from .tools import Tool, ToolContext, simulated
from .world import World

JSON = Any


@dataclass(frozen=True, slots=True)
class RecordedCall:
    tool: str
    arguments: Mapping[str, JSON]
    result: JSON = None
    error: str | None = None


@dataclass(slots=True)
class Cassette:
    name: str = "recording"
    calls: list[RecordedCall] = field(default_factory=list)
    initial_state: Mapping[str, JSON] = field(default_factory=dict)

    def record(self, tool: str, arguments: Mapping[str, JSON], result: JSON = None, error: str | None = None) -> None:
        self.calls.append(RecordedCall(tool, deepcopy(dict(arguments)), deepcopy(result), error))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(
                {"schema": "ordeal.cassette/v1", "name": self.name, "initial_state": self.initial_state,
                 "calls": [asdict(c) for c in self.calls]},
                indent=2, sort_keys=True,
            ) + "\n"
        )

    @classmethod
    def load(cls, path: str | Path) -> "Cassette":
        source = Path(path)
        if source.stat().st_size > 16 * 1024 * 1024:
            raise ValueError("Cassette exceeds 16 MiB")
        return cls.from_dict(json.loads(source.read_text()))

    @classmethod
    def from_dict(cls, data: Mapping[str, JSON]) -> "Cassette":
        if not isinstance(data, dict) or data.get("schema") != "ordeal.cassette/v1":
            raise ValueError("Expected ordeal.cassette/v1")
        calls = data.get("calls")
        if not isinstance(calls, list) or len(calls) > 100000:
            raise ValueError("Invalid cassette call list")
        for row in calls:
            if (not isinstance(row, dict) or not isinstance(row.get("tool"), str) or not row["tool"]
                    or not isinstance(row.get("arguments"), dict) or set(row)-{"tool", "arguments", "result", "error"}
                    or (row.get("error") is not None and not isinstance(row["error"], str))):
                raise ValueError("Invalid cassette call")
        if not isinstance(data.get("initial_state", {}), dict):
            raise ValueError("Cassette initial_state must be an object")
        canonical_json(data)
        return cls(name=str(data.get("name", "recording")), initial_state=deepcopy(data.get("initial_state", {})),
                   calls=[RecordedCall(**deepcopy(row)) for row in calls])

    def to_world(self, *, strict_order: bool = True) -> World:
        queues: dict[str, list[RecordedCall]] = {}
        for call in self.calls:
            queues.setdefault(call.tool, []).append(call)
        replay_key = "cassette:" + str(id(self))

        def make_handler(name: str) -> Callable[[Mapping[str, JSON], ToolContext], JSON]:
            def handler(arguments: Mapping[str, JSON], _ctx: ToolContext) -> JSON:
                cursor = _ctx.world.replay_cursors.setdefault(replay_key, {"positions": {n: 0 for n in queues}, "global": 0})
                positions = cursor["positions"]
                global_position = cursor["global"]
                if name not in queues or positions[name] >= len(queues[name]):
                    raise AssertionError(f"cassette has no remaining call for {name!r}")
                call = queues[name][positions[name]]
                if strict_order:
                    if global_position >= len(self.calls) or self.calls[global_position].tool != name:
                        expected = self.calls[global_position].tool if global_position < len(self.calls) else "<end>"
                        raise AssertionError(f"cassette order mismatch: expected {expected!r}, got {name!r}")
                if canonical_json(arguments) != canonical_json(call.arguments):
                    raise AssertionError(
                        f"cassette argument mismatch for {name}: expected {call.arguments!r}, got {dict(arguments)!r}"
                    )
                positions[name] += 1
                cursor["global"] += 1
                if call.error:
                    raise RuntimeError(call.error)
                return deepcopy(call.result)
            return handler

        tools = [simulated(name, make_handler(name), description=f"Recorded {name} fixture") for name in queues]
        return World(self.name, initial_state=self.initial_state, tools=tools)


@dataclass(slots=True)
class Recorder:
    cassette: Cassette

    async def call(
        self,
        name: str,
        fn: Callable[..., JSON | Awaitable[JSON]],
        /,
        **arguments: JSON,
    ) -> JSON:
        try:
            value = fn(**arguments)
            if inspect.isawaitable(value):
                value = await value
        except Exception as exc:
            self.cassette.record(name, arguments, error=f"{type(exc).__name__}: {exc}")
            raise
        self.cassette.record(name, arguments, result=value)
        return value


def record_tool(tool: Tool, cassette: Cassette) -> Tool:
    """Wrap an existing tool so real/simulated calls are captured into a replayable cassette."""
    original = tool.handler
    if original is None:
        raise ValueError(f"tool {tool.name!r} has no handler")

    async def handler(arguments: Mapping[str, JSON], ctx: ToolContext) -> JSON:
        try:
            value = original(arguments, ctx)
            if inspect.isawaitable(value):
                value = await value
        except Exception as exc:
            cassette.record(tool.name, arguments, error=f"{type(exc).__name__}: {exc}")
            raise
        cassette.record(tool.name, arguments, result=value)
        return value

    return Tool(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
        output_schema=tool.output_schema,
        mode=tool.mode,
        handler=handler,
        mutate=tool.mutate,
    )
