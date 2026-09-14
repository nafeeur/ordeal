from __future__ import annotations

import json
from typing import Any
import asyncio
from weakref import WeakKeyDictionary

from .world import World

JSON = Any


def tool_manifest(world: World) -> list[dict[str, JSON]]:
    return [
        {
            "name": tool.name,
            "description": tool.description or tool.name,
            "inputSchema": dict(tool.input_schema),
            **({"outputSchema": dict(tool.output_schema)} if tool.output_schema is not None else {}),
        }
        for tool in world.tools
    ]


def create_mcp_server(world: World, *, name: str | None = None, version: str = "0.3.0") -> Any:
    """Expose an Ordeal World through the official MCP Python SDK v2 low-level Server.

    The low-level API is intentional: Ordeal already owns exact JSON Schemas, so no framework
    should re-infer them from synthetic Python function signatures.
    """
    try:
        from mcp.server import Server
        from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool as MCPTool
    except ImportError as exc:
        raise RuntimeError("install ordeal-agent[mcp] to expose a World as MCP") from exc

    direct_run = world.spawn("mcp-direct")
    direct_lock = asyncio.Lock()
    sessions = WeakKeyDictionary()

    def session_runtime(ctx):
        if ctx is None:
            return direct_run, direct_lock  # Direct in-process contract calls only.
        session = getattr(ctx, "session", None)
        if session is None:
            raise RuntimeError("MCP request has no session identity; refusing to share simulated state")
        if session not in sessions:
            sessions[session] = (world.spawn("mcp-session"), asyncio.Lock())
        return sessions[session]
    manifests = tool_manifest(world)
    mcp_tools = [
        MCPTool(
            name=item["name"],
            description=item["description"],
            input_schema=item["inputSchema"],
            **({"output_schema": item["outputSchema"]} if "outputSchema" in item else {}),
        )
        for item in manifests
    ]

    async def list_tools(_ctx: Any, _params: Any) -> Any:
        return ListToolsResult(tools=mcp_tools)

    async def call_tool(_ctx: Any, params: Any) -> Any:
        run, lock = session_runtime(_ctx)
        async with lock:
            result = await run.call_tool(params.name, params.arguments or {})
        text = result if isinstance(result, str) else json.dumps(result, sort_keys=True, default=str)
        kwargs: dict[str, JSON] = {"content": [TextContent(type="text", text=text)]}
        if isinstance(result, dict):
            kwargs["structured_content"] = result
        return CallToolResult(**kwargs)

    return Server(name or f"ordeal-{world.name}", version=version, on_list_tools=list_tools, on_call_tool=call_tool)
