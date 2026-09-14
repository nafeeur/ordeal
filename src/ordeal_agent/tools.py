from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import inspect
from typing import Any, Awaitable, Callable, Mapping, get_type_hints

from jsonschema import Draft202012Validator

JSON = Any
ToolHandler = Callable[[Mapping[str, JSON], "ToolContext"], JSON | Awaitable[JSON]]
MutationHandler = Callable[[Mapping[str, JSON], JSON, "ToolContext"], None | Awaitable[None]]


class ToolMode(str, Enum):
    SIMULATED = "simulated"
    PASSTHROUGH = "passthrough"


@dataclass(slots=True)
class ToolContext:
    world: Any
    scenario_name: str
    call_index: int


@dataclass(slots=True)
class Tool:
    name: str
    description: str = ""
    input_schema: Mapping[str, JSON] = field(default_factory=lambda: {"type": "object"})
    output_schema: Mapping[str, JSON] | None = None
    mode: ToolMode = ToolMode.SIMULATED
    handler: ToolHandler | None = None
    mutate: MutationHandler | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Tool name cannot be empty")
        self.mode = ToolMode(self.mode)
        Draft202012Validator.check_schema(dict(self.input_schema))
        if self.output_schema is not None:
            Draft202012Validator.check_schema(dict(self.output_schema))

    def validate_input(self, arguments: Mapping[str, JSON]) -> None:
        Draft202012Validator(dict(self.input_schema)).validate(dict(arguments))

    def validate_output(self, output: JSON) -> None:
        if self.output_schema is not None:
            Draft202012Validator(dict(self.output_schema)).validate(output)

    async def invoke(self, arguments: Mapping[str, JSON], context: ToolContext) -> JSON:
        self.validate_input(arguments)
        if self.handler is None:
            raise RuntimeError(f"tool {self.name!r} has no handler")
        output = self.handler(arguments, context)
        if inspect.isawaitable(output):
            output = await output
        self.validate_output(output)
        if self.mutate is not None:
            mutated = self.mutate(arguments, output, context)
            if inspect.isawaitable(mutated):
                await mutated
        return output


def simulated(
    name: str,
    handler: ToolHandler,
    *,
    description: str = "",
    input_schema: Mapping[str, JSON] | None = None,
    output_schema: Mapping[str, JSON] | None = None,
    mutate: MutationHandler | None = None,
) -> Tool:
    return Tool(
        name=name,
        description=description,
        input_schema=input_schema or {"type": "object"},
        output_schema=output_schema,
        mode=ToolMode.SIMULATED,
        handler=handler,
        mutate=mutate,
    )


def passthrough(
    name: str,
    handler: ToolHandler,
    *,
    description: str = "",
    input_schema: Mapping[str, JSON] | None = None,
    output_schema: Mapping[str, JSON] | None = None,
    mutate: MutationHandler | None = None,
) -> Tool:
    return Tool(
        name=name,
        description=description,
        input_schema=input_schema or {"type": "object"},
        output_schema=output_schema,
        mode=ToolMode.PASSTHROUGH,
        handler=handler,
        mutate=mutate,
    )


def _annotation_schema(annotation: Any) -> dict[str, JSON]:
    """Small dependency-free JSON Schema mapper for common Python annotations."""
    from types import UnionType
    from typing import get_args, get_origin, Literal, Union

    if annotation is inspect.Signature.empty or annotation is Any:
        return {}
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        return {"enum": list(args)}
    if origin in (list, tuple, set):
        return {"type": "array", "items": _annotation_schema(args[0]) if args else {}}
    if origin is dict:
        return {"type": "object"}
    if origin in (Union, UnionType):
        schemas = [_annotation_schema(a) for a in args]
        return {"anyOf": schemas}
    return {
        str: {"type": "string"},
        int: {"type": "integer"},
        float: {"type": "number"},
        bool: {"type": "boolean"},
        dict: {"type": "object"},
        list: {"type": "array"},
        type(None): {"type": "null"},
    }.get(annotation, {})


def function_tool(
    fn: Callable[..., JSON | Awaitable[JSON]],
    *,
    name: str | None = None,
    description: str | None = None,
    output_schema: Mapping[str, JSON] | None = None,
    passthrough_mode: bool = False,
) -> Tool:
    """Turn a normal annotated Python function into a schema-validated Ordeal Tool.

    A parameter named ``ctx`` receives the ToolContext and is omitted from the model-facing schema.
    """
    signature = inspect.signature(fn)
    try:
        hints = get_type_hints(fn, include_extras=True)
    except (NameError, TypeError) as exc:
        raise ValueError(f"Cannot resolve annotations for {fn.__name__}; supply a Tool with an explicit schema") from exc
    properties: dict[str, JSON] = {}
    required: list[str] = []
    accepts_ctx = "ctx" in signature.parameters
    for param_name, param in signature.parameters.items():
        if param_name == "ctx":
            continue
        if param.kind in {inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            raise ValueError("Function tools require named parameters; positional-only, *args, and **kwargs need an explicit Tool")
        properties[param_name] = _annotation_schema(hints.get(param_name, param.annotation))
        if param.default is inspect.Signature.empty:
            required.append(param_name)
    input_schema: dict[str, JSON] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        input_schema["required"] = required

    async def handler(arguments: Mapping[str, JSON], context: ToolContext) -> JSON:
        kwargs = dict(arguments)
        if accepts_ctx:
            kwargs["ctx"] = context
        value = fn(**kwargs)
        if inspect.isawaitable(value):
            value = await value
        return value

    constructor = passthrough if passthrough_mode else simulated
    return constructor(
        name or fn.__name__,
        handler,
        description=description if description is not None else (inspect.getdoc(fn) or ""),
        input_schema=input_schema,
        output_schema=output_schema,
    )


def fixture_tool(
    name: str,
    fixtures: Mapping[str, JSON],
    *,
    key: str,
    description: str = "",
) -> Tool:
    """Create a deterministic simulated lookup tool backed by a mapping."""
    def handler(arguments: Mapping[str, JSON], _context: ToolContext) -> JSON:
        fixture_key = str(arguments[key])
        if fixture_key not in fixtures:
            raise KeyError(f"no fixture for {name}({key}={fixture_key!r})")
        return fixtures[fixture_key]

    return simulated(
        name,
        handler,
        description=description,
        input_schema={
            "type": "object",
            "properties": {key: {"type": "string"}},
            "required": [key],
            "additionalProperties": False,
        },
    )
