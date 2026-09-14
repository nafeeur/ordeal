from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any

from ..agents import AgentRequest, AgentResponse


@dataclass(slots=True)
class MethodAgentAdapter:
    """Version-tolerant adapter for framework objects exposing run/invoke/ainvoke."""

    target: Any
    name: str
    version: str = "dev"
    framework: str = "generic"

    async def run(self, request: AgentRequest) -> AgentResponse:
        if hasattr(self.target, "ainvoke"):
            value = self.target.ainvoke(request.instruction)
        elif hasattr(self.target, "run"):
            value = self.target.run(request.instruction)
        elif hasattr(self.target, "invoke"):
            value = self.target.invoke(request.instruction)
        elif callable(self.target):
            value = self.target(request.instruction)
        else:
            raise TypeError(f"{type(self.target)!r} has no supported execution method")
        if inspect.isawaitable(value):
            value = await value
        output = getattr(value, "final_output", getattr(value, "output", getattr(value, "content", value)))
        return AgentResponse(output, {"framework": self.framework})
