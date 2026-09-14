from __future__ import annotations

from copy import copy
from dataclasses import dataclass
import json
from typing import Any

from ..agents import AgentRequest, AgentResponse
from ..tools import Tool


@dataclass(slots=True)
class OpenAIAgentsAdapter:
    agent: Any
    name: str
    version: str = "dev"

    async def run(self, request: AgentRequest) -> AgentResponse:
        try:
            from agents import Runner
        except ImportError as exc:
            raise RuntimeError("install ordeal-agent[openai-agents]") from exc

        # Fail closed: run a shallow copy whose visible function tools are entirely backed by the
        # current Ordeal World. This prevents a test from accidentally executing the agent's real
        # production function tools.
        if getattr(self.agent, "handoffs", None) or getattr(self.agent, "mcp_servers", None):
            raise RuntimeError("Rebind handoff agents and MCP servers explicitly before simulation; implicit real-tool escape routes are blocked")
        run_agent = copy(self.agent)
        try:
            run_agent.tools = [proxy_tool(tool, request.runtime) for tool in request.runtime.world.definition.tools]
        except Exception as exc:
            raise RuntimeError("could not bind OpenAI Agents tools to the Ordeal World") from exc

        result = await Runner.run(run_agent, request.instruction)
        metadata: dict[str, Any] = {"framework": "openai-agents"}
        usage = getattr(getattr(result, "context_wrapper", None), "usage", None) or getattr(result, "usage", None)
        if usage is not None:
            if hasattr(usage, "model_dump"):
                usage = usage.model_dump()
            elif not isinstance(usage, dict):
                usage = {
                    k: getattr(usage, k)
                    for k in ("input_tokens", "output_tokens", "total_tokens")
                    if hasattr(usage, k)
                }
            metadata["usage"] = usage
        return AgentResponse(output=getattr(result, "final_output", result), metadata=metadata)


def proxy_tool(tool: Tool, runtime: Any) -> Any:
    """Create an OpenAI Agents FunctionTool backed by an Ordeal simulated tool."""
    try:
        from agents import FunctionTool
    except ImportError as exc:
        raise RuntimeError("install ordeal-agent[openai-agents]") from exc

    async def invoke(_ctx: Any, args_json: str) -> Any:
        arguments = json.loads(args_json)
        result = await runtime.call_with(tool.name, arguments)
        if isinstance(result, str):
            return result
        return json.dumps(result, sort_keys=True)

    return FunctionTool(
        name=tool.name,
        description=tool.description or tool.name,
        params_json_schema=dict(tool.input_schema),
        on_invoke_tool=invoke,
    )
