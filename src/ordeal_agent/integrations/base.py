from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..agents import Agent


class FrameworkAdapter(Protocol):
    framework: str
    def can_handle(self, agent: Any) -> bool: ...
    def wrap(self, agent: Any, *, name: str | None = None, version: str = "dev") -> Agent: ...


@dataclass(slots=True)
class AdapterRegistry:
    adapters: list[FrameworkAdapter]

    def wrap(self, agent: Any, *, name: str | None = None, version: str = "dev") -> Agent:
        for adapter in self.adapters:
            if adapter.can_handle(agent):
                return adapter.wrap(agent, name=name, version=version)
        raise TypeError(f"no framework adapter recognized {type(agent)!r}")


def adapt(agent: Any, *, name: str | None = None, version: str = "dev") -> Agent:
    """Best-effort zero-config adapter for common agent framework objects."""
    module = type(agent).__module__.lower()
    resolved_name = name or getattr(agent, "name", type(agent).__name__)
    if module == "agents" or module.startswith("agents."):
        from .openai_agents import OpenAIAgentsAdapter
        return OpenAIAgentsAdapter(agent, resolved_name, version)
    if "langgraph" in module or "langchain" in module:
        from .langchain import LangGraphAdapter
        return LangGraphAdapter(agent, resolved_name, version)
    if "pydantic_ai" in module:
        from .pydantic_ai import PydanticAIAdapter
        return PydanticAIAdapter(agent, resolved_name, version)
    if "crewai" in module:
        from .crewai import CrewAIAdapter
        return CrewAIAdapter(agent, resolved_name, version)
    if "autogen" in module:
        from .autogen import AutoGenAdapter
        return AutoGenAdapter(agent, resolved_name, version)
    if "llama_index" in module or "llamaindex" in module:
        from .llamaindex import LlamaIndexAdapter
        return LlamaIndexAdapter(agent, resolved_name, version)
    if "smolagents" in module:
        from .smolagents import SmolagentsAdapter
        return SmolagentsAdapter(agent, resolved_name, version)
    if "strands" in module:
        from .strands import StrandsAdapter
        return StrandsAdapter(agent, resolved_name, version)
    if "google.adk" in module:
        from .google_adk import GoogleADKAdapter
        return GoogleADKAdapter(agent, resolved_name, version)
    from .generic import MethodAgentAdapter
    return MethodAgentAdapter(agent, resolved_name, version, "generic")
