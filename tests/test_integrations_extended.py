from types import SimpleNamespace
import sys
import types

import pytest

from ordeal_agent import OutputContains, ToolCalled, Runner, Scenario, Suite, World, create_mcp_server, simulated, tool_manifest
from ordeal_agent.integrations import AnthropicToolLoopAgent, adapt


@pytest.mark.parametrize(
    "module, method, expected_framework",
    [
        ("crewai.agent", "run", "crewai"),
        ("autogen_agentchat.agents", "run", "autogen"),
        ("llama_index.core.agent", "run", "llamaindex"),
        ("smolagents.agents", "run", "smolagents"),
        ("strands.agent", "run", "strands"),
        ("google.adk.runners", "run", "google-adk"),
    ],
)
@pytest.mark.asyncio
async def test_framework_auto_adapters(module, method, expected_framework):
    async def runner(*args, **kwargs):
        return SimpleNamespace(output="ok")

    cls = type("FrameworkAgent", (), {"__module__": module, "name": expected_framework, method: runner})
    target = cls()
    agent = adapt(target, version="1")
    report = await Runner().run_suite(agent, Suite.of("s", Scenario("x", "hello", World("w"), assertions=[OutputContains("ok")])))
    assert report.pass_rate == 1.0
    assert report.agent_name == expected_framework


class _Block:
    def __init__(self, type, **kwargs):
        self.type = type
        self.__dict__.update(kwargs)

    def model_dump(self):
        return {"type": self.type, **{k: v for k, v in self.__dict__.items() if k != "type"}}


@pytest.mark.asyncio
async def test_anthropic_tool_loop_routes_tool_calls_to_world():
    responses = [
        SimpleNamespace(
            content=[_Block("tool_use", id="1", name="lookup", input={"id": "A"})],
            usage=SimpleNamespace(input_tokens=10, output_tokens=2),
        ),
        SimpleNamespace(
            content=[_Block("text", text="done")],
            usage=SimpleNamespace(input_tokens=4, output_tokens=3),
        ),
    ]

    class Messages:
        async def create(self, **kwargs):
            return responses.pop(0)

    client = SimpleNamespace(messages=Messages())
    world = World("w", tools=[simulated("lookup", lambda args, _ctx: {"id": args["id"]})])
    agent = AnthropicToolLoopAgent(client, "claude-test")
    result = await Runner().run_scenario(agent, Scenario("anthropic", "find", world, assertions=[ToolCalled("lookup"), OutputContains("done")]))
    assert result.verdict.value == "pass"
    assert result.trajectory.tool_names() == ["lookup"]
    assert result.usage.total_tokens == 19


def test_mcp_manifest_preserves_exact_schemas():
    world = World("w", tools=[simulated(
        "lookup", lambda _a, _c: {"ok": True},
        input_schema={"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
    )])
    manifest = tool_manifest(world)
    assert manifest[0]["inputSchema"]["required"] == ["id"]
    assert manifest[0]["outputSchema"]["required"] == ["ok"]


@pytest.mark.asyncio
async def test_mcp_v2_low_level_server_contract(monkeypatch):
    class MCPTool:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class ListToolsResult:
        def __init__(self, tools): self.tools = tools
    class TextContent:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class CallToolResult:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class Server:
        def __init__(self, name, version, on_list_tools, on_call_tool):
            self.name, self.version = name, version
            self.on_list_tools, self.on_call_tool = on_list_tools, on_call_tool

    mcp = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    types_mod = types.ModuleType("mcp.types")
    server_mod.Server = Server
    types_mod.Tool = MCPTool
    types_mod.ListToolsResult = ListToolsResult
    types_mod.TextContent = TextContent
    types_mod.CallToolResult = CallToolResult
    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.server", server_mod)
    monkeypatch.setitem(sys.modules, "mcp.types", types_mod)

    world = World("calc", tools=[simulated("add", lambda a, _c: {"sum": a["a"] + a["b"]})])
    server = create_mcp_server(world)
    listed = await server.on_list_tools(None, None)
    assert listed.tools[0].name == "add"
    called = await server.on_call_tool(None, SimpleNamespace(name="add", arguments={"a": 2, "b": 3}))
    assert called.structured_content == {"sum": 5}


@pytest.mark.asyncio
async def test_framework_neutral_proxy_callable_has_schema_signature_and_routes():
    from ordeal_agent.agents import ToolRuntime
    from ordeal_agent.integrations import proxy_callable
    import inspect

    tool = simulated(
        "lookup",
        lambda args, _ctx: {"id": args["id"]},
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["id"],
        },
    )
    run = World("w", tools=[tool]).spawn("proxy")
    fn = proxy_callable(tool, ToolRuntime(run))
    sig = inspect.signature(fn)
    assert sig.parameters["id"].annotation is str
    assert sig.parameters["id"].default is inspect.Parameter.empty
    assert sig.parameters["limit"].default is None
    assert await fn(id="A") == {"id": "A"}


@pytest.mark.asyncio
async def test_openai_adapter_rebinds_tools_fail_closed(monkeypatch):
    class FunctionTool:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeRunner:
        @staticmethod
        async def run(agent, instruction):
            assert instruction == "do it"
            assert [tool.name for tool in agent.tools] == ["safe_lookup"]
            result = await agent.tools[0].on_invoke_tool(None, '{"id":"A"}')
            return SimpleNamespace(final_output=result)

    agents_mod = types.ModuleType("agents")
    agents_mod.FunctionTool = FunctionTool
    agents_mod.Runner = FakeRunner
    monkeypatch.setitem(sys.modules, "agents", agents_mod)

    AgentClass = type("Agent", (), {"__module__": "agents", "name": "openai", "tools": [object()]})
    raw = AgentClass()
    world = World("w", tools=[simulated("safe_lookup", lambda args, _ctx: {"id": args["id"]})])
    wrapped = adapt(raw, version="1")
    result = await Runner().run_scenario(wrapped, Scenario("oa", "do it", world, assertions=[ToolCalled("safe_lookup")]))
    assert result.verdict.value == "pass"
    assert result.trajectory.tool_names() == ["safe_lookup"]
    assert raw.tools and not hasattr(raw.tools[0], "name")  # original agent remains untouched
