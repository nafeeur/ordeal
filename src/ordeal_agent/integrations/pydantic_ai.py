from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..agents import AgentRequest, AgentResponse


@dataclass(slots=True)
class PydanticAIAdapter:
    agent: Any
    name: str
    version: str = "dev"

    async def run(self, request: AgentRequest) -> AgentResponse:
        if not hasattr(self.agent, "run"):
            raise TypeError("expected a pydantic-ai Agent-like object with run()")
        result = await self.agent.run(request.instruction)
        output = getattr(result, "output", getattr(result, "data", result))
        return AgentResponse(output, {"framework": "pydantic-ai"})
