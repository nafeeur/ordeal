import pytest
from jsonschema import ValidationError

from ordeal_agent import CallableAgent, Runner, Scenario, Verdict, World, simulated


@pytest.mark.asyncio
async def test_tool_input_schema_is_enforced():
    tool = simulated(
        "lookup",
        lambda args, ctx: {"ok": True},
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    )
    world = World("w", tools=[tool])

    async def fn(request):
        await request.runtime.call("lookup", id="not-an-int")
        return "x"

    result = await Runner().run_scenario(CallableAgent(fn), Scenario("bad-schema", "x", world))
    assert result.verdict == Verdict.ERROR
    assert "not of type" in (result.error or "")
