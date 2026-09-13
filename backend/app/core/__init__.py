"""Stable, technology-neutral contracts used at Ordeal's trust boundary."""

from .contracts import (
    ACTION_SCHEMA_VERSION,
    CONTRACT_SCHEMA_VERSION,
    ActionEnvelope,
    EvidenceRecord,
    VerificationVerdict,
    WorldSnapshot,
    normalize_action,
)
from .protocols import FaultAdapter, RuntimeAdapter, StateAdapter, TargetAdapter, Verifier

__all__ = [
    "ACTION_SCHEMA_VERSION",
    "CONTRACT_SCHEMA_VERSION",
    "ActionEnvelope",
    "EvidenceRecord",
    "FaultAdapter",
    "RuntimeAdapter",
    "StateAdapter",
    "TargetAdapter",
    "VerificationVerdict",
    "Verifier",
    "WorldSnapshot",
    "normalize_action",
]
