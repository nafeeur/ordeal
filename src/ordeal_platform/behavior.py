from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import math
from statistics import mean
from typing import Any


def percentile(values, q):
    if not values:
        return None
    data = sorted(values)
    pos = (len(data) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return data[lo] + (data[hi] - data[lo]) * (pos - lo)


def tools(trace: dict) -> list[str]:
    return [str(e.get("name", "")) for e in trace.get("trajectory", {}).get("events", []) if e.get("kind") == "tool_call"]


def signature(trace):
    names = tools(trace)
    return hashlib.sha256("\x00".join(names).encode()).hexdigest()


def classify(trace: dict) -> list[str]:
    out = set()
    names = tools(trace)
    if any(n >= 4 for n in Counter(names).values()):
        out.add("repeated_tool_calls")
    for event in trace.get("trajectory", {}).get("events", []):
        if event.get("kind") == "error":
            name = str(event.get("name", "")).lower()
            if "timeout" in name:
                out.add("timeout")
            elif "not_found" in name:
                out.add("unknown_tool")
            elif "argument" in name or "schema" in name:
                out.add("tool_arguments")
            else:
                out.add("execution_error")
    for check in trace.get("checks", []):
        if check.get("verdict") == "fail":
            name = str(check.get("name", "")).lower()
            out.add("ordering_violation" if "order" in name else "assertion_failure")
    if trace.get("status") in {"error", "fail"} and not out:
        out.add("unclassified_failure")
    return sorted(out)


def analytics(records: list[dict]) -> dict:
    clusters = defaultdict(list)
    tool_counts = Counter()
    failures = Counter()
    statuses = Counter()
    for record in records:
        data = record.get("data", record)
        clusters[tuple(tools(data))].append(record)
        tool_counts.update(tools(data))
        failures.update(classify(data))
        statuses.update([record.get("status", data.get("status", "unset"))])
    latencies = [r.get("duration_ms", 0) for r in records]
    costs = [r["cost_usd"] for r in records if r.get("cost_usd") is not None]
    return {
        "runs": len(records), "statuses": dict(statuses),
        "pass_rate": statuses["pass"] / len(records) if records else None,
        "latency": {"p50_ms": percentile(latencies, .5), "p95_ms": percentile(latencies, .95), "p99_ms": percentile(latencies, .99)},
        "cost_usd": sum(costs) if costs else None, "cost_coverage": len(costs) / len(records) if records else None,
        "tool_frequency": dict(tool_counts), "failure_taxonomy": dict(failures),
        "clusters": [{"sequence": list(k), "count": len(v), "fraction": len(v) / len(records),
                      "example_trace_id": v[0].get("id"), "signature": signature(v[0].get("data", v[0]))}
                     for k, v in sorted(clusters.items(), key=lambda item: -len(item[1]))[:100]],
        "clustering_method": "exact tool-sequence grouping; not semantic clustering",
    }


def drift(baseline: list[dict], candidate: list[dict]) -> dict:
    if not baseline or not candidate:
        return {"verdict": "incomplete", "reason": "Both cohorts require at least one trace"}
    a = Counter(tuple(tools(r.get("data", r))) for r in baseline)
    b = Counter(tuple(tools(r.get("data", r))) for r in candidate)
    keys = set(a) | set(b)
    js = 0.0
    for key in keys:
        p, q = a[key] / len(baseline), b[key] / len(candidate)
        m = (p + q) / 2
        if p:
            js += .5 * p * math.log2(p / m)
        if q:
            js += .5 * q * math.log2(q / m)
    return {"verdict": "pass", "jensen_shannon_divergence": js,
            "baseline_runs": len(baseline), "candidate_runs": len(candidate),
            "new_sequences": [{"sequence": list(k), "count": b[k]} for k in b if k not in a],
            "removed_sequences": [{"sequence": list(k), "count": a[k]} for k in a if k not in b],
            "note": "Descriptive drift, not a statistical significance test"}


def compare_reports(baseline: dict, candidate: dict, min_pass_rate=.95, max_pass_rate_drop=0) -> dict:
    def keyed(report):
        out = {}
        for row in report.get("results", []):
            key = (row.get("scenario"), row.get("case_id", "default"), row.get("repetition", 0))
            if key in out:
                raise ValueError("Duplicate scenario/case/repetition in report")
            out[key] = row
        return out
    a, b = keyed(baseline), keyed(candidate)
    changes, regressions = [], []
    for key in sorted(set(a) | set(b), key=str):
        before, after = a.get(key), b.get(key)
        item = {"case": list(key), "before": before.get("verdict") if before else None, "after": after.get("verdict") if after else None}
        if before and not after:
            regressions.append(item | {"reason": "removed case"})
        elif before and after and before.get("verdict") == "pass" and after.get("verdict") != "pass":
            regressions.append(item | {"reason": "verdict regression"})
        if before and after:
            x = before.get("trajectory", {}).get("structural_fingerprint")
            y = after.get("trajectory", {}).get("structural_fingerprint")
            if x != y:
                changes.append(item | {"reason": "trajectory changed"})
    ap = sum(x.get("verdict") == "pass" for x in a.values()) / len(a) if a else 0
    bp = sum(x.get("verdict") == "pass" for x in b.values()) / len(b) if b else 0
    ok = bool(a and b) and not regressions and bp >= min_pass_rate and ap - bp <= max_pass_rate_drop + 1e-12
    return {"passed": ok, "baseline_pass_rate": ap, "candidate_pass_rate": bp,
            "delta": bp - ap, "regressions": regressions, "behavior_changes": changes,
            "added_cases": [list(k) for k in b if k not in a], "removed_cases": [list(k) for k in a if k not in b]}


def calibration(labels: list[dict]) -> dict:
    if not labels:
        return {"count": 0, "accuracy": None, "precision": None, "recall": None, "cohen_kappa": None, "disagreements": []}
    for r in labels:
        if type(r.get("human")) is not bool or type(r.get("judge")) is not bool:
            raise ValueError("Calibration labels must be boolean human/judge pairs")
    tp = sum(r["human"] and r["judge"] for r in labels)
    tn = sum(not r["human"] and not r["judge"] for r in labels)
    fp = sum(not r["human"] and r["judge"] for r in labels)
    fn = sum(r["human"] and not r["judge"] for r in labels)
    n = len(labels)
    observed = (tp + tn) / n
    expected = ((tp + fn) * (tp + fp) + (tn + fp) * (tn + fn)) / n ** 2
    return {"count": n, "accuracy": observed, "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "cohen_kappa": (observed - expected) / (1 - expected) if expected < 1 else None,
            "confusion": {"tp": tp, "tn": tn, "fp": fp, "fn": fn},
            "disagreements": [r for r in labels if r["human"] != r["judge"]]}
