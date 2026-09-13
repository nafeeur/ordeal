"""Capability-based adapter discovery and fail-closed conformance checks."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable


ADAPTER_KINDS = {"target", "state", "runtime", "fault", "verifier"}


@dataclass(frozen=True)
class AdapterDescriptor:
    kind: str
    name: str
    capabilities: tuple[str, ...]
    description: str
    maturity: str = "experimental"
    deterministic: bool = False

    def to_dict(self) -> dict:
        value = asdict(self)
        value["capabilities"] = list(self.capabilities)
        return value


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str], AdapterDescriptor] = {}

    def register(self, descriptor: AdapterDescriptor) -> AdapterDescriptor:
        if descriptor.kind not in ADAPTER_KINDS:
            raise ValueError(f"unsupported adapter kind: {descriptor.kind}")
        self._adapters[(descriptor.kind, descriptor.name)] = descriptor
        return descriptor

    def list(self, kind: str | None = None) -> list[dict]:
        if kind is not None and kind not in ADAPTER_KINDS:
            raise ValueError(f"unsupported adapter kind: {kind}")
        rows = [item for (item_kind, _), item in self._adapters.items() if kind is None or item_kind == kind]
        return [item.to_dict() for item in sorted(rows, key=lambda row: (row.kind, row.name))]

    def check(self, kind: str, name: str, required: Iterable[str] = ()) -> dict:
        descriptor = self._adapters.get((kind, name))
        required_set = set(required)
        if descriptor is None:
            return {"compatible": False, "adapter": {"kind": kind, "name": name}, "required": sorted(required_set), "missing": sorted(required_set), "reason": "adapter is not registered"}
        missing = required_set.difference(descriptor.capabilities)
        return {
            "compatible": not missing,
            "adapter": descriptor.to_dict(),
            "required": sorted(required_set),
            "missing": sorted(missing),
            "reason": "" if not missing else "adapter does not satisfy the verification boundary",
        }

    def require(self, kind: str, name: str, required: Iterable[str] = ()) -> AdapterDescriptor:
        report = self.check(kind, name, required)
        if not report["compatible"]:
            raise ValueError(report["reason"] or "adapter is incompatible")
        return self._adapters[(kind, name)]


registry = AdapterRegistry()

for built_in in (
    AdapterDescriptor("target", "mock", ("invoke", "tool_calls", "seeded"), "Deterministic in-process target used by simulated worlds.", deterministic=True),
    AdapterDescriptor("target", "http", ("invoke", "tool_proxy", "timeouts"), "External target reached through an HTTP contract."),
    AdapterDescriptor("target", "openai_compatible", ("invoke", "tool_calls", "streamless"), "OpenAI-compatible model endpoint target."),
    AdapterDescriptor("target", "observed_trace", ("observe", "action_envelope", "offline"), "Pre-recorded execution trace; no target invocation.", deterministic=True),
    AdapterDescriptor("state", "memory", ("snapshot", "restore", "hash", "exact_mutation"), "Canonical in-memory world ledger.", deterministic=True),
    AdapterDescriptor("runtime", "local", ("execute", "seed", "concurrency", "replay"), "Local asyncio execution runtime.", deterministic=True),
    AdapterDescriptor("runtime", "kafka", ("enqueue", "lease", "workers", "at_least_once"), "Experimental Kafka-backed distributed runtime."),
    AdapterDescriptor("fault", "simulated", ("before_execution", "after_commit", "seeded", "replay"), "Seeded simulator fault phases.", deterministic=True),
    AdapterDescriptor("verifier", "deterministic", ("policies", "evidence_chain", "replay_bundle", "fail_closed"), "Built-in deterministic runtime verifier.", deterministic=True),
):
    registry.register(built_in)
