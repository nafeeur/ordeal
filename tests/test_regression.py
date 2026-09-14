import pytest

from ordeal_agent import CallableAgent, OutputContains, Runner, Scenario, Suite, World, compare_to_baseline


@pytest.mark.asyncio
async def test_regression_detects_pass_to_fail():
    world = World("w")

    async def good(_request):
        return "approved"

    scenario = Scenario("approval", "approve", world, assertions=[OutputContains("approved")])
    suite = Suite.of("s", scenario)
    baseline_report = await Runner().run_suite(CallableAgent(good, version="1"), suite)
    baseline = baseline_report.to_dict()

    async def bad(_request):
        return "denied"

    current = await Runner().run_suite(CallableAgent(bad, version="2"), suite)
    regression = compare_to_baseline(current, baseline)
    assert not regression.ok
    assert any(item.kind == "verdict" for item in regression.regressions)
