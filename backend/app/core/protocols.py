"""Structural adapter interfaces; implementations need no Ordeal base class."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .contracts import ActionEnvelope, WorldSnapshot


@runtime_checkable
class TargetAdapter(Protocol):
    async def invoke(self, action: ActionEnvelope) -> ActionEnvelope: ...


@runtime_checkable
class StateAdapter(Protocol):
    def snapshot(self) -> WorldSnapshot: ...
    def restore(self, snapshot: WorldSnapshot) -> None: ...


@runtime_checkable
class RuntimeAdapter(Protocol):
    async def execute(self, target: TargetAdapter, actions: list[ActionEnvelope]) -> list[ActionEnvelope]: ...


@runtime_checkable
class FaultAdapter(Protocol):
    def apply(self, action: ActionEnvelope, phase: str) -> ActionEnvelope: ...


@runtime_checkable
class Verifier(Protocol):
    def verify(self, execution: dict[str, Any]) -> dict[str, Any]: ...
