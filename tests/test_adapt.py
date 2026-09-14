import pytest

from ordeal_agent import OutputContains, Runner, Scenario, Suite, World
from ordeal_agent.integrations import adapt


class FakeCrewAgent:
    __module__ = "crewai.agent"
    name = "crew"

    async def run(self, instruction):
        return f"ran: {instruction}"


@pytest.mark.asyncio
async def test_auto_adapter_for_framework_like_objects():
    agent = adapt(FakeCrewAgent(), version="v2")
    report = await Runner().run_suite(agent, Suite.of("s", Scenario("x", "hello", World("w"), assertions=[OutputContains("ran: hello")])))
    assert report.pass_rate == 1.0
    assert report.agent_name == "crew"
    assert report.agent_version == "v2"
