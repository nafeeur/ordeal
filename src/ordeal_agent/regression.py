from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from statistics import median
from typing import Any, Mapping

from .models import SuiteReport, Verdict

JSON = Any


@dataclass(frozen=True, slots=True)
class Regression:
    scenario: str
    repetition: int
    kind: str
    message: str
    before: JSON = None
    after: JSON = None
    case_id: str = "default"


@dataclass(slots=True)
class RegressionReport:
    baseline_agent_version: str
    current_agent_version: str
    baseline_pass_rate: float
    current_pass_rate: float
    regressions: list[Regression] = field(default_factory=list)
    changes: list[Regression] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.regressions

    def to_dict(self) -> dict[str, JSON]:
        return {
            "schema": "ordeal.regression-report/v2",
            "baseline_agent_version": self.baseline_agent_version,
            "current_agent_version": self.current_agent_version,
            "baseline_pass_rate": self.baseline_pass_rate,
            "current_pass_rate": self.current_pass_rate,
            "ok": self.ok,
            "regressions": [asdict(r) for r in self.regressions],
            "changes": [asdict(r) for r in self.changes],
        }


def save_baseline(report: SuiteReport, path: str | Path) -> None:
    Path(path).write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")


def load_baseline(path: str | Path) -> dict[str, JSON]:
    return json.loads(Path(path).read_text())


def _key(row: Mapping[str, JSON]) -> tuple[str, str, int]:
    return (str(row["scenario"]), str(row.get("case_id", "default")), int(row.get("repetition", 0)))


def compare_to_baseline(
    current: SuiteReport,
    baseline: Mapping[str, JSON],
    *,
    max_pass_rate_drop: float = 0.0,
    max_latency_ratio: float | None = 2.0,
    max_cost_ratio: float | None = 2.0,
    max_token_ratio: float | None = 2.0,
    fail_on_removed: bool = True,
) -> RegressionReport:
    baseline_results = {_key(r): r for r in baseline.get("results", [])}
    current_keys = {(r.scenario, r.case_id, r.repetition) for r in current.results}
    report = RegressionReport(
        baseline_agent_version=str(baseline.get("agent", {}).get("version", "unknown")),
        current_agent_version=current.agent_version,
        baseline_pass_rate=float(baseline.get("pass_rate", 0.0)),
        current_pass_rate=current.pass_rate,
    )

    for result in current.results:
        key = (result.scenario, result.case_id, result.repetition)
        before = baseline_results.get(key)
        if before is None:
            report.changes.append(
                Regression(result.scenario, result.repetition, "new_scenario", "new scenario result", case_id=result.case_id)
            )
            continue

        before_verdict = str(before.get("verdict"))
        after_verdict = result.verdict.value
        if before_verdict == Verdict.PASS.value and after_verdict != Verdict.PASS.value:
            report.regressions.append(
                Regression(
                    result.scenario, result.repetition, "verdict", "previous PASS no longer passes",
                    before_verdict, after_verdict, result.case_id,
                )
            )
        elif before_verdict != after_verdict:
            report.changes.append(
                Regression(
                    result.scenario, result.repetition, "verdict_change", "verdict changed",
                    before_verdict, after_verdict, result.case_id,
                )
            )

        before_traj = before.get("trajectory", {})
        before_struct = before_traj.get("structural_fingerprint")
        after_struct = result.trajectory.structural_fingerprint
        if before_struct and before_struct != after_struct:
            report.changes.append(
                Regression(
                    result.scenario,
                    result.repetition,
                    "behavior_shape",
                    "tool/state/error trajectory structure changed",
                    before_traj.get("events", []),
                    [{"kind": e.kind, "name": e.name, "payload": e.payload} for e in result.trajectory.events],
                    result.case_id,
                )
            )

        if before_traj.get("content_fingerprint") and before_traj.get("content_fingerprint") != result.trajectory.content_fingerprint:
            report.changes.append(
                Regression(
                    result.scenario, result.repetition, "behavior_content",
                    "trajectory content changed", before_traj.get("content_fingerprint"),
                    result.trajectory.content_fingerprint, result.case_id,
                )
            )

        if max_latency_ratio is not None:
            before_ms = float(before.get("duration_ms", 0.0) or 0.0)
            if before_ms > 0 and result.duration_ms > before_ms * max_latency_ratio:
                report.regressions.append(
                    Regression(
                        result.scenario, result.repetition, "latency",
                        f"latency exceeded {max_latency_ratio:.2f}x baseline",
                        before_ms, result.duration_ms, result.case_id,
                    )
                )

        before_usage = before.get("usage", {}) or {}
        if max_cost_ratio is not None and before_usage.get("cost_usd") is not None and "cost_usd" not in result.usage.measured:
            report.regressions.append(Regression(result.scenario, result.repetition, "cost_unmeasured",
                "Previously measured cost is no longer available", before_usage["cost_usd"], None, result.case_id))
        if max_token_ratio is not None and before_usage.get("total_tokens") is not None and "total_tokens" not in result.usage.measured:
            report.regressions.append(Regression(result.scenario, result.repetition, "tokens_unmeasured",
                "Previously measured token usage is no longer available", before_usage["total_tokens"], None, result.case_id))
        if max_cost_ratio is not None:
            before_cost = float(before_usage.get("cost_usd", 0.0) or 0.0)
            if before_cost > 0 and result.usage.cost_usd > before_cost * max_cost_ratio:
                report.regressions.append(
                    Regression(
                        result.scenario, result.repetition, "cost",
                        f"cost exceeded {max_cost_ratio:.2f}x baseline",
                        before_cost, result.usage.cost_usd, result.case_id,
                    )
                )
        if max_token_ratio is not None:
            before_tokens = int(before_usage.get("total_tokens", 0) or 0)
            if before_tokens > 0 and result.usage.total_tokens > before_tokens * max_token_ratio:
                report.regressions.append(
                    Regression(
                        result.scenario, result.repetition, "tokens",
                        f"token usage exceeded {max_token_ratio:.2f}x baseline",
                        before_tokens, result.usage.total_tokens, result.case_id,
                    )
                )

    removed = sorted(set(baseline_results) - current_keys)
    for scenario, case_id, repetition in removed:
        item = Regression(
            scenario, repetition, "removed_scenario", "baseline scenario result is missing",
            baseline_results[(scenario, case_id, repetition)], None, case_id,
        )
        (report.regressions if fail_on_removed else report.changes).append(item)

    allowed = report.baseline_pass_rate - max_pass_rate_drop
    if report.current_pass_rate < allowed:
        report.regressions.append(
            Regression(
                "<suite>", 0, "pass_rate", f"pass rate dropped below allowed {allowed:.3f}",
                report.baseline_pass_rate, report.current_pass_rate,
            )
        )
    return report


def median_duration(report: SuiteReport) -> float:
    return median([r.duration_ms for r in report.results]) if report.results else 0.0
