from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from ..agents import AgentRequest, AgentResponse
from ..tools import Tool


@dataclass(slots=True)
class LangGraphAdapter:
    graph: Any
    name: str
    version: str = "dev"
    input_key: str = "messages"

    async def run(self, request: AgentRequest) -> AgentResponse:
        payload = {self.input_key: [("user", request.instruction)]}
        if hasattr(self.graph, "ainvoke"):
            result = await self.graph.ainvoke(payload)
        else:
            result = self.graph.invoke(payload)
        return AgentResponse(result, {"framework": "langgraph"})


def proxy_tool(tool: Tool, runtime: Any) -> Any:
    """Create a LangChain StructuredTool that routes calls into the Ordeal world."""
    try:
        from langchain_core.tools import StructuredTool
    except ImportError as exc:
        raise RuntimeError("install ordeal-agent[langchain]") from exc

    async def coroutine(**kwargs: Any) -> Any:
        return await runtime.call_with(tool.name, kwargs)

    def sync_stub(**_kwargs: Any) -> Any:
        raise RuntimeError("Ordeal simulated LangChain tools are async; use ainvoke/async agents")

    return StructuredTool.from_function(
        func=sync_stub,
        coroutine=coroutine,
        name=tool.name,
        description=tool.description or tool.name,
        args_schema=dict(tool.input_schema),
    )
