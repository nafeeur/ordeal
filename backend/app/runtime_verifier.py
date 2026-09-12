"""Deterministic verification for software whose behavior is chosen at runtime.

The model is not a source of truth.  This module consumes observed boundary events,
checks them against an explicit contract, and returns a replayable evidence bundle.
It intentionally has no model dependency and makes no semantic guesses.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any

from .engine import evaluate_assertions, get_path, stable_hash


SUPPORTED_EVENT_KINDS = {"read", "write", "delete", "call", "transform", "visualize", "decision"}
SUPPORTED_POLICIES = {
    "deny",
    "require_before",
    "data_boundary",
    "max_occurrences",
    "require_dependency",
    "transformation",
    "state_assertion",
}


def _event_value(event: dict[str, Any], path: str) -> Any:
    if path.startswith("data."):
        return get_path(event.get("data", {}), path[5:])
    return get_path(event, path)


def _matches(event: dict[str, Any], selector: dict[str, Any] | None) -> bool:
    """Match a small, auditable selector language; no arbitrary expressions."""
    for path, expected in (selector or {}).items():
        actual = _event_value(event, path)
        if isinstance(expected, dict):
            if "in" in expected and actual not in expected["in"]:
                return False
            if "not_in" in expected and actual in expected["not_in"]:
                return False
            if "contains" in expected:
                values = actual if isinstance(actual, (list, tuple, set)) else []
                if expected["contains"] not in values:
                    return False
        elif actual != expected:
            return False
    return True


def _same_values(left: dict[str, Any], right: dict[str, Any], mapping: dict[str, str]) -> bool:
    return all(_event_value(left, a) == _event_value(right, b) for a, b in mapping.items())


def _ancestors(event_id: str, by_id: dict[str, dict[str, Any]]) -> set[str]:
    found: set[str] = set()
    stack = list(by_id[event_id].get("depends_on", []))
    while stack:
        current = stack.pop()
        if current in found or current not in by_id:
            continue
        found.add(current)
        stack.extend(by_id[current].get("depends_on", []))
    return found


def _result(policy: dict[str, Any], passed: bool, actual: Any, evidence: list[str], reason: str = "") -> dict[str, Any]:
    return {
        "policy": policy.get("name") or policy.get("type", "unnamed"),
        "type": policy.get("type"),
        "severity": policy.get("severity", "critical"),
        "passed": passed,
        "actual": actual,
        "evidence": evidence,
        "reason": reason,
    }


def _validate_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    normalized: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_seq = -1
    for index, raw in enumerate(events):
        event = copy.deepcopy(raw)
        if not isinstance(event, dict):
            problems.append({"event": f"event-{index + 1:04d}", "reason": "event must be an object"})
            event = {"kind": None, "data": {}}
        event_id = str(event.get("id") or f"event-{index + 1:04d}")
        event["id"] = event_id
        event.setdefault("seq", index + 1)
        event.setdefault("depends_on", [])
        event.setdefault("data", {})
        event.setdefault("classifications", [])
        if not isinstance(event["depends_on"], list):
            problems.append({"event": event_id, "reason": "depends_on must be a list"})
            event["depends_on"] = []
        if not isinstance(event["classifications"], list):
            problems.append({"event": event_id, "reason": "classifications must be a list"})
            event["classifications"] = []
        if event_id in seen:
            problems.append({"event": event_id, "reason": "duplicate event id"})
        if not isinstance(event["seq"], int) or event["seq"] <= previous_seq:
            problems.append({"event": event_id, "reason": "sequence must be strictly increasing"})
        if event.get("kind") not in SUPPORTED_EVENT_KINDS:
            problems.append({"event": event_id, "reason": f"unsupported event kind: {event.get('kind')}"})
        missing = [dep for dep in event["depends_on"] if dep not in seen]
        if missing:
            problems.append({"event": event_id, "reason": "dependency must reference an earlier event", "dependencies": missing})
        seen.add(event_id)
        previous_seq = event["seq"] if isinstance(event["seq"], int) else previous_seq
        normalized.append(event)
    return normalized, problems


def _hash_chain(events: list[dict[str, Any]], initial_state: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    head = stable_hash({"initial_state": initial_state})
    chain = []
    for event in events:
        head = stable_hash({"previous": head, "event": event})
        chain.append({"event_id": event["id"], "hash": head})
    return chain, head


def _evaluate_policy(
    policy: dict[str, Any],
    events: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    final_state: dict[str, Any],
) -> dict[str, Any]:
    kind = policy.get("type")
    targets = [event for event in events if _matches(event, policy.get("target") or policy.get("match"))]

    if kind == "deny":
        ids = [event["id"] for event in targets]
        return _result(policy, not ids, len(ids), ids, "forbidden action observed" if ids else "")

    if kind == "require_before":
        prerequisite = policy.get("prerequisite", {})
        same_by = policy.get("same_by", {})
        failures = []
        evidence = []
        for target in targets:
            candidates = [event for event in events if event["seq"] < target["seq"] and _matches(event, prerequisite)]
            if same_by:
                candidates = [event for event in candidates if _same_values(target, event, same_by)]
            if not candidates:
                failures.append(target["id"])
            else:
                evidence.extend([candidates[-1]["id"], target["id"]])
        return _result(policy, not failures, failures, evidence, "required prior event not observed" if failures else "")

    if kind == "data_boundary":
        protected = set(policy.get("classifications", []))
        allowed = set(policy.get("allowed_services", []))
        violations = [
            event["id"] for event in targets
            if protected.intersection(event.get("classifications", [])) and event.get("service") not in allowed
        ]
        return _result(policy, not violations, violations, [e["id"] for e in targets], "protected data crossed an untrusted boundary" if violations else "")

    if kind == "max_occurrences":
        group_by = policy.get("group_by", [])
        maximum = int(policy.get("value", 1))
        grouped: dict[tuple[Any, ...], list[str]] = defaultdict(list)
        for event in targets:
            key = tuple(stable_hash(_event_value(event, path)) for path in group_by) if group_by else ("all",)
            grouped[key].append(event["id"])
        failures = [ids for ids in grouped.values() if len(ids) > maximum]
        return _result(policy, not failures, failures, [event["id"] for event in targets], "occurrence limit exceeded" if failures else "")

    if kind == "require_dependency":
        source = policy.get("source", {})
        failures = []
        evidence = []
        for target in targets:
            ancestor_events = [by_id[event_id] for event_id in _ancestors(target["id"], by_id)]
            matching = [event for event in ancestor_events if _matches(event, source)]
            if not matching:
                failures.append(target["id"])
            else:
                evidence.extend([matching[0]["id"], target["id"]])
        return _result(policy, not failures, failures, evidence, "target has no declared provenance path to required source" if failures else "")

    if kind == "transformation":
        source_selector = policy.get("source", {})
        mappings = policy.get("mappings", [])
        failures = []
        evidence = []
        for target in targets:
            sources = [by_id[event_id] for event_id in _ancestors(target["id"], by_id) if _matches(by_id[event_id], source_selector)]
            if not sources:
                failures.append({"event": target["id"], "reason": "source not in dependency graph"})
                continue
            source = sorted(sources, key=lambda item: item["seq"])[-1]
            evidence.extend([source["id"], target["id"]])
            for mapping in mappings:
                op = mapping.get("op", "copy")
                destination_value = _event_value(target, mapping["to"])
                if op == "copy":
                    expected = _event_value(source, mapping["from"])
                elif op == "concat":
                    expected = mapping.get("separator", " ").join(str(_event_value(source, path) or "") for path in mapping.get("from", []))
                elif op == "constant":
                    expected = mapping.get("value")
                else:
                    failures.append({"event": target["id"], "mapping": mapping, "reason": f"unsupported transform op: {op}"})
                    continue
                if destination_value != expected:
                    failures.append({"event": target["id"], "field": mapping["to"], "expected": expected, "actual": destination_value})
        return _result(policy, not failures, failures, evidence, "transformation contract violated" if failures else "")

    if kind == "state_assertion":
        assertion = policy.get("assertion", {})
        grade = evaluate_assertions(final_state, events, [assertion], {"entries": []})[0]
        return _result(policy, grade["passed"], grade.get("actual"), [], "final state invariant violated" if not grade["passed"] else "")

    return _result(policy, False, None, [], f"unsupported policy type: {kind}")


def verify_runtime_execution(spec: dict[str, Any]) -> dict[str, Any]:
    """Verify one observed model-native execution against an explicit contract."""
    contract = copy.deepcopy(spec.get("contract") or {})
    initial_state = copy.deepcopy(spec.get("initial_state") or {})
    final_state = copy.deepcopy(spec.get("final_state") or {})
    events, integrity_problems = _validate_events(spec.get("events") or [])
    chain, chain_head = _hash_chain(events, initial_state)
    by_id = {event["id"]: event for event in events}

    policies = contract.get("policies") or []
    results = []
    unresolved = []
    for policy in policies:
        if not isinstance(policy, dict):
            unresolved.append({"policy": "unnamed", "reason": "policy must be an object"})
            continue
        if policy.get("type") not in SUPPORTED_POLICIES:
            unresolved.append({"policy": policy.get("name", "unnamed"), "reason": f"unsupported policy type: {policy.get('type')}"})
            continue
        try:
            results.append(_evaluate_policy(policy, events, by_id, final_state))
        except (KeyError, TypeError, ValueError) as exc:
            unresolved.append({"policy": policy.get("name", "unnamed"), "reason": f"invalid policy: {exc}"})

    expected_head = spec.get("expected_chain_head")
    if expected_head and expected_head != chain_head:
        integrity_problems.append({"reason": "trace chain head does not match expected value", "expected": expected_head, "actual": chain_head})
    if not policies:
        unresolved.append({"reason": "contract contains no policies"})
    if not events:
        unresolved.append({"reason": "trace contains no observed events"})

    blocking = [result for result in results if not result["passed"]]
    if integrity_problems or blocking:
        verdict = "FAIL"
    elif unresolved:
        verdict = "INCOMPLETE"
    else:
        verdict = "PASS"

    replay_bundle = {
        "contract": contract,
        "initial_state": initial_state,
        "final_state": final_state,
        "events": events,
        "expected_chain_head": chain_head,
    }
    return {
        "verdict": verdict,
        "blocking": verdict != "PASS",
        "scope": "verified observed behavior within the declared contract",
        "execution": spec.get("execution") or "runtime-execution",
        "contract": contract.get("name") or "unnamed-contract",
        "summary": {
            "events": len(events),
            "policies": len(policies),
            "passed": sum(result["passed"] for result in results),
            "violated": sum(not result["passed"] for result in results),
            "unresolved": len(unresolved),
            "integrity_problems": len(integrity_problems),
        },
        "policy_results": results,
        "violations": [result for result in results if not result["passed"]],
        "unresolved": unresolved,
        "integrity_problems": integrity_problems,
        "evidence": {
            "initial_state_hash": stable_hash(initial_state),
            "final_state_hash": stable_hash(final_state),
            "chain_head": chain_head,
            "chain": chain,
        },
        "fingerprint": stable_hash(replay_bundle),
        "replay_bundle": replay_bundle,
    }
