from __future__ import annotations

import asyncio
import time
from typing import Iterable

from .agents import Agent, AgentRequest, ToolRuntime
from .models import CheckResult, ScenarioResult, SuiteReport, Usage, Verdict
from .scenario import Budget, Case, Scenario, Suite


class Runner:
    def __init__(self, *, fail_on_agent_error: bool = False) -> None:
        self.fail_on_agent_error = fail_on_agent_error

    async def run_scenario(
        self,
        agent: Agent,
        scenario: Scenario,
        repetition: int = 0,
        case: Case | None = None,
    ) -> ScenarioResult:
        case = case or Case("default")
        instruction, world_def, metadata = scenario.resolved(case)
        world = world_def.spawn(scenario.name, seed_offset=repetition, faults=scenario.faults)
        runtime = ToolRuntime(world)
        start = time.perf_counter()
        usage = Usage.from_mapping(None)
        try:
            response = await agent.run(AgentRequest(instruction=instruction, runtime=runtime, metadata=metadata))
            usage = Usage.from_mapping(response.metadata.get("usage") if hasattr(response.metadata, "get") else None)
            trajectory = world.finish(response.output)
            checks = [await assertion.evaluate(trajectory) for assertion in scenario.assertions]
            elapsed_ms = (time.perf_counter() - start) * 1000
            checks.extend(self._budget_checks(scenario.limits, usage, elapsed_ms))
            if not checks:
                checks = [CheckResult("behavior_assertions", Verdict.INCOMPLETE, "No assertions or measurable budgets were configured")]
            verdict = self._combine(checks)
            error = None
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000
            trajectory = world.finish(None)
            trajectory.add("error", "agent_exception", type=type(exc).__name__, message=str(exc))
            checks = [CheckResult("agent_execution", Verdict.ERROR, f"{type(exc).__name__}: {exc}")]
            verdict = Verdict.ERROR
            error = f"{type(exc).__name__}: {exc}"
            if self.fail_on_agent_error:
                raise
        return ScenarioResult(
            scenario=scenario.name,
            case_id=case.id,
            repetition=repetition,
            verdict=verdict,
            checks=checks,
            trajectory=trajectory,
            duration_ms=elapsed_ms,
            usage=usage,
            error=error,
        )

    def run_suite_sync(self, agent: Agent, suite: Suite) -> SuiteReport:
        return asyncio.run(self.run_suite(agent, suite))

    async def run_suite(self, agent: Agent, suite: Suite) -> SuiteReport:
        started = time.time()
        if not suite.scenarios or not 1 <= suite.concurrency <= 1024:
            raise ValueError("Suite requires scenarios and concurrency between 1 and 1024")
        names = [scenario.name for scenario in suite.scenarios]
        if len(names) != len(set(names)):
            raise ValueError("Scenario names must be unique within a suite")
        specs = []
        for scenario in suite.scenarios:
            if scenario.repetitions < 1 or not scenario.cases:
                raise ValueError("Scenarios require at least one case and repetition")
            ids = [case.id for case in scenario.cases]
            if len(ids) != len(set(ids)):
                raise ValueError("Case ids must be unique")
            specs.extend((scenario, case, rep) for case in scenario.cases for rep in range(scenario.repetitions))
        # Bound runnable tasks rather than creating one asyncio.Task per expanded case.
        pending = iter(enumerate(specs))
        results = [None] * len(specs)
        async def worker():
            for index, (scenario, case, repetition) in pending:
                results[index] = await self.run_scenario(agent, scenario, repetition, case)
        tasks = [asyncio.create_task(worker()) for _ in range(min(suite.concurrency, len(specs)))]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return SuiteReport(
            suite=suite.name,
            agent_name=agent.name,
            agent_version=agent.version,
            results=list(results),
            started_at=started,
            finished_at=time.time(),
        )

    @staticmethod
    def _budget_checks(budget: Budget, usage: Usage, elapsed_ms: float) -> list[CheckResult]:
        checks: list[CheckResult] = []
        specs = [
            ("budget_total_tokens", budget.max_total_tokens, usage.total_tokens, "tokens"),
            ("budget_input_tokens", budget.max_input_tokens, usage.input_tokens, "tokens"),
            ("budget_output_tokens", budget.max_output_tokens, usage.output_tokens, "tokens"),
            ("budget_cost", budget.max_cost_usd, usage.cost_usd, "USD"),
            ("budget_latency", budget.max_latency_ms, elapsed_ms, "ms"),
        ]
        for name, maximum, actual, unit in specs:
            if maximum is None:
                continue
            metric = {"budget_total_tokens": "total_tokens", "budget_input_tokens": "input_tokens",
                      "budget_output_tokens": "output_tokens", "budget_cost": "cost_usd"}.get(name)
            if metric is not None and metric not in usage.measured:
                checks.append(CheckResult(name, Verdict.INCOMPLETE, f"No measured {metric}; budget cannot be verified"))
                continue
            ok = actual <= maximum
            checks.append(
                CheckResult(
                    name,
                    Verdict.PASS if ok else Verdict.FAIL,
                    f"{actual:.6g} {unit} <= {maximum:.6g} {unit}" if ok else f"{actual:.6g} {unit} exceeds {maximum:.6g} {unit}",
                    {"actual": actual, "maximum": maximum, "unit": unit},
                )
            )
        return checks

    @staticmethod
    def _combine(checks: Iterable[CheckResult]) -> Verdict:
        checks = list(checks)
        if not checks:
            return Verdict.INCOMPLETE
        verdicts = {check.verdict for check in checks}
        if Verdict.ERROR in verdicts:
            return Verdict.ERROR
        if Verdict.FAIL in verdicts:
            return Verdict.FAIL
        if Verdict.INCOMPLETE in verdicts:
            return Verdict.INCOMPLETE
        return Verdict.PASS
