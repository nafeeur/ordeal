from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Callable, Mapping

from ..agents import AgentRequest, AgentResponse


def _output(value: Any) -> Any:
    for attr in ("final_output", "output", "data", "content", "result", "raw"):
        if hasattr(value, attr):
            candidate = getattr(value, attr)
            if candidate is not None:
                return candidate
    if hasattr(value, "messages"):
        messages = getattr(value, "messages")
        if messages:
            last = messages[-1]
            return getattr(last, "content", getattr(last, "text", last))
    return value


@dataclass(slots=True)
class FlexibleFrameworkAdapter:
    target: Any
    name: str
    version: str = "dev"
    framework: str = "generic"
    methods: tuple[str, ...] = ("ainvoke", "run", "invoke")
    task_keyword: str | None = None
    extractor: Callable[[Any], Any] = _output

    async def run(self, request: AgentRequest) -> AgentResponse:
        method = next((getattr(self.target, m) for m in self.methods if hasattr(self.target, m)), None)
        if method is None and callable(self.target):
            method = self.target
        if method is None:
            raise TypeError(f"{type(self.target)!r} has no supported execution method")
        if self.task_keyword:
            value = method(**{self.task_keyword: request.instruction})
        else:
            value = method(request.instruction)
        if inspect.isawaitable(value):
            value = await value
        metadata: dict[str, Any] = {"framework": self.framework}
        usage = _extract_usage(value)
        if usage:
            metadata["usage"] = usage
        return AgentResponse(self.extractor(value), metadata)


def _extract_usage(value: Any) -> Mapping[str, Any] | None:
    usage = getattr(value, "usage", None) or getattr(value, "usage_metadata", None)
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    elif not isinstance(usage, Mapping):
        usage = {
            key: getattr(usage, key)
            for key in ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens")
            if hasattr(usage, key)
        }
    return dict(usage)
