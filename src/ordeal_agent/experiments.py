from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .agents import Agent
from .models import SuiteReport
from .regression import RegressionReport, compare_to_baseline
from .runner import Runner
from .scenario import Suite

JSON = Any


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    agent: Agent
    tags: Mapping[str, JSON] = field(default_factory=dict)


@dataclass(slots=True)
class ExperimentReport:
    reports: dict[str, SuiteReport]
    comparisons: dict[str, RegressionReport]

    @property
    def ranking(self) -> list[tuple[str, float, float | None, float]]:
        rows = [
            (name, report.pass_rate, report.usage.cost_usd if "cost_usd" in report.usage.measured else None, report.latency["p50_ms"])
            for name, report in self.reports.items()
        ]
        return sorted(rows, key=lambda row: (-row[1], row[2] if row[2] is not None else float("inf"), row[3]))

    def to_dict(self) -> dict[str, JSON]:
        return {
            "schema": "ordeal.experiment-report/v1",
            "ranking": [
                {"variant": name, "pass_rate": pass_rate, "cost_usd": cost, "p50_ms": latency}
                for name, pass_rate, cost, latency in self.ranking
            ],
            "reports": {name: report.to_dict() for name, report in self.reports.items()},
            "comparisons": {name: report.to_dict() for name, report in self.comparisons.items()},
        }


async def run_experiment(
    suite: Suite,
    variants: Sequence[Variant],
    *,
    baseline: str | None = None,
    runner: Runner | None = None,
) -> ExperimentReport:
    if not variants:
        raise ValueError("at least one variant is required")
    if len({v.name for v in variants}) != len(variants):
        raise ValueError("Variant names must be unique")
    runner = runner or Runner()
    reports: dict[str, SuiteReport] = {}
    for variant in variants:
        reports[variant.name] = await runner.run_suite(variant.agent, suite)
    baseline_name = baseline or variants[0].name
    if baseline_name not in reports:
        raise KeyError(f"unknown baseline variant {baseline_name!r}")
    baseline_dict = reports[baseline_name].to_dict()
    comparisons = {
        name: compare_to_baseline(report, baseline_dict, fail_on_removed=False)
        for name, report in reports.items()
        if name != baseline_name
    }
    return ExperimentReport(reports, comparisons)
