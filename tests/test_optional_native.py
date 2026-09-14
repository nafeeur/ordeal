"""Native optional-dependency smoke tests.

These are skipped in core CI and enabled one framework at a time with
ORDEAL_OPTIONAL_FRAMEWORK. The GitHub Actions optional matrix installs the
corresponding extra before running this file.
"""

import os
from types import SimpleNamespace

import pytest

from ordeal_agent import ToolRuntime, World, create_mcp_server, simulated

FRAMEWORK = os.environ.get("ORDEAL_OPTIONAL_FRAMEWORK")
pytestmark = pytest.mark.skipif(not FRAMEWORK, reason="optional framework matrix only")


def runtime_and_tool():
    tool = simulated(
        "add",
        lambda args, _ctx: {"sum": args["a"] + args["b"]},
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"sum": {"type": "integer"}},
            "required": ["sum"],
            "additionalProperties": False,
        },
    )
    world = World("native", tools=[tool])
    return ToolRuntime(world.spawn("native")), tool, world


@pytest.mark.asyncio
async def test_selected_optional_framework_contract():
    runtime, tool, world = runtime_and_tool()

    if FRAMEWORK == "anthropic":
        import anthropic
        assert anthropic is not None
        return

    if FRAMEWORK == "mcp":
        from mcp import Client
        server = create_mcp_server(world)
        async with Client(server) as client:
            listed = await client.list_tools()
            assert any(item.name == "add" for item in listed.tools)
            result = await client.call_tool("add", {"a": 2, "b": 3})
            assert result.structured_content == {"sum": 5}
        return

    if FRAMEWORK == "openai-agents":
        from ordeal_agent.integrations.openai_agents import proxy_tool
        native = proxy_tool(tool, runtime)
        output = await native.on_invoke_tool(None, '{"a":2,"b":3}')
        assert "5" in str(output)
        return

    if FRAMEWORK == "langchain":
        from ordeal_agent.integrations.langchain import proxy_tool
        native = proxy_tool(tool, runtime)
        assert await native.ainvoke({"a": 2, "b": 3}) == {"sum": 5}
        return

    from ordeal_agent.integrations import proxy_tools
    native = proxy_tools(FRAMEWORK, [tool], runtime)
    assert len(native) == 1
