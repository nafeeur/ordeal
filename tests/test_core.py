import pytest

from ordeal_agent import (
    CallableAgent,
    Fault,
    OutputContains,
    RealToolBlockedError,
    Runner,
    Scenario,
    StateEquals,
    Suite,
    ToolCalled,
    ToolOrder,
    Verdict,
    World,
    passthrough,
    simulated,
)


@pytest.mark.asyncio
async def test_world_and_behavior_assertions():
    def read(args, ctx):
        return {"balance": ctx.world.get("balance")}

    def debit(args, ctx):
        return {"ok": True}

    def mutate(args, result, ctx):
        ctx.world.set("balance", ctx.world.get("balance") - args["amount"])

    world = World(
        "bank",
        {"balance": 100},
        [simulated("read", read), simulated("debit", debit, mutate=mutate)],
    )

    async def fn(request):
        await request.runtime.call("read")
        await request.runtime.call("debit", amount=30)
        return "done"

    agent = CallableAgent(fn, name="bank-agent", version="1")
    scenario = Scenario(
        "debit",
        "debit 30",
        world,
        assertions=[ToolCalled("debit"), ToolOrder("read", "debit"), StateEquals("balance", 70), OutputContains("done")],
    )
    report = await Runner().run_suite(agent, Suite.of("suite", scenario))
    assert report.pass_rate == 1.0
    assert report.results[0].verdict == Verdict.PASS
    assert report.results[0].trajectory.tool_names() == ["read", "debit"]


@pytest.mark.asyncio
async def test_fault_injection_becomes_error():
    world = World("w", tools=[simulated("service", lambda args, ctx: {"ok": True})])

    async def fn(request):
        await request.runtime.call("service")
        return "done"

    scenario = Scenario("fault", "x", world, faults=[Fault("service", on_call=1, error="down")])
    result = await Runner().run_scenario(CallableAgent(fn), scenario)
    assert result.verdict == Verdict.ERROR
    assert "down" in (result.error or "")


@pytest.mark.asyncio
async def test_passthrough_is_fail_closed():
    world = World("w", tools=[passthrough("real", lambda args, ctx: "should not execute")])

    async def fn(request):
        return await request.runtime.call("real")

    result = await Runner().run_scenario(CallableAgent(fn), Scenario("blocked", "x", world))
    assert result.verdict == Verdict.ERROR
    assert "blocked" in (result.error or "")


@pytest.mark.asyncio
async def test_function_tool_auto_schema_and_context():
    from ordeal_agent import function_tool

    def lookup(order_id: str, count: int = 1, ctx=None):
        return {"order_id": order_id, "count": count, "world": ctx.world.definition.name}

    tool = function_tool(lookup)
    assert tool.input_schema["properties"]["order_id"] == {"type": "string"}
    assert tool.input_schema["properties"]["count"] == {"type": "integer"}
    assert tool.input_schema["required"] == ["order_id"]
    world = World("auto", tools=[tool])

    async def fn(request):
        return await request.runtime.call("lookup", order_id="A")

    result = await Runner().run_scenario(CallableAgent(fn), Scenario("auto", "x", world, assertions=[ToolCalled("lookup")]))
    assert result.verdict == Verdict.PASS
    assert result.trajectory.final_output["world"] == "auto"
