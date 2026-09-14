import asyncio
from types import SimpleNamespace

import pytest

from ordeal_agent import (
    ToolCalled,
    AgentResponse,
    CallableAgent,
    Cassette,
    Fault,
    OutputContains,
    Recorder,
    Runner,
    Scenario,
    Suite,
    Variant,
    Verdict,
    World,
    diff_reports,
    run_experiment,
    shrink_faults,
    simulated,
)


@pytest.mark.asyncio
async def test_parameterized_dataset_budgets_metrics_and_flakiness():
    async def agent_fn(request):
        name = request.metadata["variables"]["name"]
        return AgentResponse(
            f"hello {name}",
            {"usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14, "cost_usd": 0.002}},
        )

    scenario = (
        Scenario("hello", "Say hello to {name}", World("w"), assertions=[OutputContains("hello")])
        .parameterize([{"id": "a", "name": "Alice"}, {"id": "b", "name": "Bob"}])
        .repeat(2)
        .budget(max_total_tokens=20, max_cost_usd=0.01, max_latency_ms=5000)
    )
    report = await Runner().run_suite(CallableAgent(agent_fn), Suite.of("s", scenario, concurrency=1))
    assert len(report.results) == 4
    assert report.pass_rate == 1.0
    assert report.usage.total_tokens == 56
    assert report.usage.cost_usd == pytest.approx(0.008)
    assert report.flaky_rate == 0.0
    assert report.latency["p95_ms"] >= 0
    assert {r.case_id for r in report.results} == {"a", "b"}


@pytest.mark.asyncio
async def test_budget_failure_is_behavior_failure():
    async def agent_fn(_request):
        return AgentResponse("ok", {"usage": {"total_tokens": 100, "cost_usd": 0.5}})

    scenario = Scenario("budget", "x", World("w")).budget(max_total_tokens=50, max_cost_usd=0.1)
    result = await Runner().run_scenario(CallableAgent(agent_fn), scenario)
    assert result.verdict == Verdict.FAIL
    assert {c.name for c in result.checks if c.verdict == Verdict.FAIL} == {"budget_total_tokens", "budget_cost"}


@pytest.mark.asyncio
async def test_cassette_records_real_calls_and_replays_as_world(tmp_path):
    cassette = Cassette("orders")
    recorder = Recorder(cassette)

    async def real_lookup(order_id: str):
        return {"id": order_id, "status": "paid"}

    value = await recorder.call("lookup_order", real_lookup, order_id="A100")
    assert value["status"] == "paid"
    path = tmp_path / "orders.json"
    cassette.save(path)
    replay = Cassette.load(path).to_world()

    async def agent_fn(request):
        return await request.runtime.call("lookup_order", order_id="A100")

    result = await Runner().run_scenario(CallableAgent(agent_fn), Scenario("replay", "x", replay, assertions=[ToolCalled("lookup_order")]))
    assert result.verdict == Verdict.PASS
    assert result.trajectory.final_output == {"id": "A100", "status": "paid"}


@pytest.mark.asyncio
async def test_fault_counterexample_shrinking_removes_irrelevant_fault():
    world = World("w", tools=[
        simulated("unused", lambda _a, _c: "x"),
        simulated("critical", lambda _a, _c: "ok"),
    ])

    async def agent_fn(request):
        await request.runtime.call("critical")
        return "done"

    scenario = Scenario(
        "faults",
        "x",
        world,
        faults=[Fault("unused", error="irrelevant"), Fault("critical", error="down")],
    )
    shrunk = await shrink_faults(CallableAgent(agent_fn), scenario)
    assert len(shrunk.minimal) == 1
    assert shrunk.minimal[0].tool == "critical"
    assert shrunk.result.verdict == Verdict.ERROR


@pytest.mark.asyncio
async def test_experiment_and_report_diff_compare_variants():
    async def good(_request):
        return AgentResponse("approved", {"usage": {"total_tokens": 5, "cost_usd": 0.001}})

    async def bad(_request):
        return AgentResponse("denied", {"usage": {"total_tokens": 30, "cost_usd": 0.02}})

    suite = Suite.of("approval", Scenario("approval", "x", World("w"), assertions=[OutputContains("approved")]))
    exp = await run_experiment(
        suite,
        [Variant("good", CallableAgent(good, version="1")), Variant("bad", CallableAgent(bad, version="2"))],
    )
    assert exp.ranking[0][0] == "good"
    assert not exp.comparisons["bad"].ok
    diff = diff_reports(exp.reports["good"].to_dict(), exp.reports["bad"].to_dict())
    assert diff.changed
    assert {c.kind for c in diff.changes} >= {"verdict", "tokens", "cost"}


@pytest.mark.asyncio
async def test_flakiness_detects_repetition_behavior_changes():
    calls = 0

    async def varying(request):
        nonlocal calls
        calls += 1
        if calls % 2:
            await request.runtime.call("a")
        else:
            await request.runtime.call("b")
        return "ok"

    world = World("w", tools=[simulated("a", lambda _a, _c: 1), simulated("b", lambda _a, _c: 1)])
    scenario = Scenario("flaky", "x", world, assertions=[OutputContains("ok")]).repeat(4)
    report = await Runner().run_suite(CallableAgent(varying), Suite.of("s", scenario, concurrency=1))
    assert report.pass_rate == 1.0
    assert report.flaky_rate == 1.0
    assert report.flakiness["flaky:default"]["unique_structures"] == 2


@pytest.mark.asyncio
async def test_fault_modes_delay_return_and_timeout():
    world = World("w", tools=[simulated("service", lambda _a, _c: {"source": "real-sim"})])

    async def run_with(fault):
        async def fn(request):
            return await request.runtime.call("service")
        return await Runner().run_scenario(CallableAgent(fn), Scenario("f", "x", world, assertions=[ToolCalled("service")], faults=[fault]))

    delayed = await run_with(Fault.delay("service", delay_ms=2))
    assert delayed.verdict == Verdict.PASS
    assert delayed.duration_ms >= 1

    returned = await run_with(Fault.returning("service", {"source": "fault"}))
    assert returned.verdict == Verdict.PASS
    assert returned.trajectory.final_output == {"source": "fault"}

    timed_out = await run_with(Fault.timeout("service"))
    assert timed_out.verdict == Verdict.ERROR
    assert "timeout" in (timed_out.error or "").lower()
