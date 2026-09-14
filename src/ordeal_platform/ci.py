from __future__ import annotations
from .behavior import compare_reports


def gate_report(report, *, baseline=None, min_pass_rate=1.0):
    if not 0 <= min_pass_rate <= 1:
        raise ValueError("min_pass_rate must be between 0 and 1")
    if not isinstance(report, dict) or not isinstance(report.get("results"), list):
        raise ValueError("A gate requires a report with a results array")
    results = report["results"]
    if any(not isinstance(r, dict) or r.get("verdict") not in {"pass", "fail", "incomplete", "error"} for r in results):
        raise ValueError("Every case requires a recognized verdict")
    counts = {v: sum(r.get("verdict") == v for r in results) for v in ("pass", "fail", "incomplete", "error")}
    rate = counts["pass"] / len(results) if results else 0
    passed = bool(results) and rate >= min_pass_rate and counts["error"] == 0 and counts["incomplete"] == 0
    comparison = compare_reports(baseline, report, min_pass_rate) if baseline else None
    if comparison and not comparison["passed"]:
        passed = False
    lines = ["## Ordeal behavior gate", "", f"**{'PASS' if passed else 'FAIL'}**", "", "| Verdict | Cases |", "|---|---:|"]
    lines += [f"| {k.upper()} | {v} |" for k, v in counts.items()]
    lines += ["", f"Pass rate: **{rate:.1%}**; required: **{min_pass_rate:.1%}**."]
    if comparison:
        lines += ["", f"Regressions: {len(comparison['regressions'])}; behavior changes: {len(comparison['behavior_changes'])}."]
        for regression in comparison["regressions"][:20]:
            # Markdown is rendered by CI; escape backticks and angle brackets in user-supplied names.
            name = str(regression["case"]).replace("`", "'").replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"- `{name}`: {regression['reason']}")
    return {"passed": passed, "counts": counts, "pass_rate": rate, "comparison": comparison, "markdown": "\n".join(lines) + "\n"}
