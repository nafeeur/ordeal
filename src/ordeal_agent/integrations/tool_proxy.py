from __future__ import annotations

import inspect
from typing import Any, Mapping

from ..tools import Tool

JSON = Any


def _python_type(schema: Mapping[str, JSON]) -> Any:
    kind = schema.get("type")
    return {"string": str, "integer": int, "number": float, "boolean": bool, "array": list, "object": dict}.get(kind, Any)


def proxy_callable(tool: Tool, runtime: Any) -> Any:
    """Create an async Python callable whose signature mirrors an Ordeal tool schema."""
    properties = dict(tool.input_schema.get("properties", {})) if isinstance(tool.input_schema, Mapping) else {}
    required = set(tool.input_schema.get("required", [])) if isinstance(tool.input_schema, Mapping) else set()

    async def invoke(**kwargs: JSON) -> JSON:
        return await runtime.call_with(tool.name, kwargs)

    invoke.__name__ = tool.name
    invoke.__doc__ = tool.description or tool.name
    parameters = []
    for name, schema in properties.items():
        default = inspect.Parameter.empty if name in required else None
        parameters.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=_python_type(schema if isinstance(schema, Mapping) else {}),
            )
        )
    invoke.__signature__ = inspect.Signature(parameters, return_annotation=Any)  # type: ignore[attr-defined]
    # Some frameworks (e.g. autogen_core) read typing.get_type_hints(), which looks at
    # __annotations__ directly rather than __signature__ — keep both in sync.
    invoke.__annotations__ = {param.name: param.annotation for param in parameters} | {"return": Any}
    return invoke


def proxy_tools(framework: str, tools: list[Tool], runtime: Any) -> list[Any]:
    """Convert Ordeal tools into a framework's native tool wrappers when available.

    Framework-specific imports are lazy; the core package never requires these dependencies.
    """
    framework = framework.lower().replace("_", "-")
    callables = [proxy_callable(tool, runtime) for tool in tools]

    if framework in {"pydantic-ai", "pydanticai", "generic"}:
        return callables
    if framework in {"autogen", "autogen-agentchat"}:
        try:
            from autogen_core.tools import FunctionTool
        except ImportError as exc:
            raise RuntimeError("install ordeal-agent[autogen]") from exc
        return [FunctionTool(fn, description=tool.description or tool.name, name=tool.name) for fn, tool in zip(callables, tools)]
    if framework in {"llamaindex", "llama-index"}:
        try:
            from llama_index.core.tools import FunctionTool
        except ImportError as exc:
            raise RuntimeError("install ordeal-agent[llamaindex]") from exc
        return [FunctionTool.from_defaults(async_fn=fn, name=tool.name, description=tool.description or tool.name) for fn, tool in zip(callables, tools)]
    if framework == "smolagents":
        try:
            from smolagents import Tool as SmolTool
        except ImportError as exc:
            raise RuntimeError("install ordeal-agent[smolagents]") from exc
        built = []
        for fn, tool in zip(callables, tools):
            props = tool.input_schema.get("properties", {})
            inputs = {
                key: {"type": (schema.get("type", "any") if isinstance(schema, Mapping) else "any"), "description": ""}
                for key, schema in props.items()
            }
            # Dynamic subclass preserves exact Ordeal name/schema while routing execution back into runtime.
            async_fn = fn

            def forward(self, _fn=async_fn, **kwargs):
                return _run_sync_proxy(_fn, kwargs)

            # smolagents validates Tool.forward by inspecting its real parameter names
            # against `inputs`; a generic **kwargs signature fails that check, so give
            # it an explicit __signature__ naming each input (`self` is auto-dropped
            # once this function is bound as an instance method).
            forward.__signature__ = inspect.Signature(
                [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
                + [inspect.Parameter(key, inspect.Parameter.KEYWORD_ONLY) for key in inputs]
            )
            attrs = {
                "name": tool.name,
                "description": tool.description or tool.name,
                "inputs": inputs,
                "output_type": "any",
                "forward": forward,
            }
            built.append(type(f"Ordeal_{tool.name}_Tool", (SmolTool,), attrs)())
        return built
    if framework == "strands":
        try:
            from strands import tool as strands_tool
        except ImportError as exc:
            raise RuntimeError("install ordeal-agent[strands]") from exc
        return [strands_tool(fn) for fn in callables]
    if framework in {"google-adk", "adk"}:
        try:
            from google.adk.tools import FunctionTool
        except ImportError as exc:
            raise RuntimeError("install ordeal-agent[google-adk]") from exc
        return [FunctionTool(fn) for fn in callables]
    if framework == "crewai":
        try:
            from crewai.tools import tool as crew_tool
        except ImportError as exc:
            raise RuntimeError("install ordeal-agent[crewai]") from exc
        return [crew_tool(tool.name)(fn) for fn, tool in zip(callables, tools)]
    raise ValueError(f"unsupported tool proxy framework {framework!r}")


def _run_sync_proxy(fn: Any, kwargs: Mapping[str, JSON]) -> JSON:
    """Smolagents tools are synchronous today; bridge only when no event loop is active."""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(fn(**dict(kwargs)))
    raise RuntimeError("synchronous framework tool called inside an active event loop")
