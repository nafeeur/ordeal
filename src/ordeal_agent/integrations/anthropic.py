from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from ..agents import AgentRequest, AgentResponse


@dataclass(slots=True)
class AnthropicToolLoopAgent:
    """Minimal Anthropic Messages tool loop backed entirely by the Ordeal ToolRuntime."""

    client: Any
    model: str
    name: str = "anthropic-agent"
    version: str = "dev"
    max_tokens: int = 2048
    max_turns: int = 16
    system: str | None = None

    async def run(self, request: AgentRequest) -> AgentResponse:
        tools = [
            {
                "name": tool.name,
                "description": tool.description or tool.name,
                "input_schema": dict(tool.input_schema),
            }
            for tool in request.runtime.world.definition.tools
        ]
        messages: list[dict[str, Any]] = [{"role": "user", "content": request.instruction}]
        total_in = total_out = 0
        for _ in range(self.max_turns):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": messages,
                "tools": tools,
            }
            if self.system:
                kwargs["system"] = self.system
            response = await self.client.messages.create(**kwargs)
            usage = getattr(response, "usage", None)
            total_in += int(getattr(usage, "input_tokens", 0) or 0)
            total_out += int(getattr(usage, "output_tokens", 0) or 0)
            blocks = list(getattr(response, "content", []))
            tool_uses = [b for b in blocks if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                text = "".join(str(getattr(b, "text", "")) for b in blocks if getattr(b, "type", None) == "text")
                return AgentResponse(
                    text,
                    {"framework": "anthropic", "usage": {"input_tokens": total_in, "output_tokens": total_out, "total_tokens": total_in + total_out, "model": self.model}},
                )
            messages.append({"role": "assistant", "content": [_block_dict(block) for block in blocks]})
            results = []
            for block in tool_uses:
                try:
                    value = await request.runtime.call_with(block.name, dict(block.input or {}))
                    content = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": content})
                except Exception as exc:
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": f"{type(exc).__name__}: {exc}", "is_error": True})
            messages.append({"role": "user", "content": results})
        raise RuntimeError(f"Anthropic tool loop exceeded max_turns={self.max_turns}")


def _block_dict(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        return block.model_dump()
    out = {"type": getattr(block, "type", "text")}
    for name in ("id", "name", "input", "text"):
        if hasattr(block, name):
            out[name] = getattr(block, name)
    return out
