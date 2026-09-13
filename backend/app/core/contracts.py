"""Canonical values exchanged between Ordeal and technology-specific adapters."""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

ACTION_SCHEMA_VERSION = "ordeal.action/v1"
CONTRACT_SCHEMA_VERSION = "ordeal.contract/v1"


def content_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


class VerificationVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class ActionEnvelope:
    """A provider- and runtime-neutral observation at a system boundary."""

    id: str
    seq: int
    kind: str
    name: str
    actor: str = "target"
    data: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    classifications: tuple[str, ...] = ()
    service: str | None = None
    adapter: str | None = None
    runtime: str | None = None
    schema_version: str = ACTION_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["depends_on"] = list(self.depends_on)
        value["classifications"] = list(self.classifications)
        return {key: item for key, item in value.items() if item is not None}

    @property
    def fingerprint(self) -> str:
        return content_hash(self.to_dict())


@dataclass(frozen=True)
class WorldSnapshot:
    state: dict[str, Any]
    adapter: str = "memory"
    revision: str | None = None

    @property
    def fingerprint(self) -> str:
        return content_hash({"adapter": self.adapter, "revision": self.revision, "state": self.state})


@dataclass(frozen=True)
class EvidenceRecord:
    kind: str
    subject: str
    value: Any
    source: str = "ordeal"

    @property
    def fingerprint(self) -> str:
        return content_hash(asdict(self))


def normalize_action(raw: dict[str, Any], index: int) -> dict[str, Any]:
    """Add the canonical envelope without discarding adapter-specific evidence."""
    event = copy.deepcopy(raw)
    event_type = str(event.get("type") or "")
    kind = event.get("kind")
    if not kind and event_type == "tool.call":
        kind = "call"
    name = event.get("name") or event.get("tool") or event.get("operation") or kind or "unknown"
    data = event.get("data")
    if data is None:
        data = {}
        if "args" in event:
            data["input"] = copy.deepcopy(event["args"])
        if "result" in event:
            data["output"] = copy.deepcopy(event["result"])
    envelope = ActionEnvelope(
        id=str(event.get("id") or f"event-{index + 1:04d}"),
        seq=event.get("seq", index + 1),
        kind=kind,
        name=str(name),
        actor=str(event.get("actor") or "target"),
        data=data,
        depends_on=tuple(event.get("depends_on") or ()) if isinstance(event.get("depends_on", []), (list, tuple)) else (),
        classifications=tuple(event.get("classifications") or ()) if isinstance(event.get("classifications", []), (list, tuple)) else (),
        service=event.get("service"),
        adapter=event.get("adapter"),
        runtime=event.get("runtime"),
        schema_version=str(event.get("schema_version") or ACTION_SCHEMA_VERSION),
    ).to_dict()
    for key, value in envelope.items():
        event.setdefault(key, value)
    return event
