"""Safe, reproducible prompt templates: names only, no expressions or attribute access."""
from __future__ import annotations
import re
from string import Formatter
from .security import fail


def fields(template: str) -> set[str]:
    found=set()
    try:
        for literal,name,spec,conversion in Formatter().parse(template):
            if name is None:continue
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',name) or spec or conversion:
                fail(422,'Prompt substitutions must be simple names, without format specs or attribute access')
            found.add(name)
    except ValueError:
        fail(422,'Malformed prompt template braces')
    return found


def render(template: str, variables: dict[str,str]) -> str:
    needed=fields(template)
    if set(variables) != needed:
        fail(422,'Prompt variables must exactly match declared template fields')
    if any(not isinstance(v,str) for v in variables.values()):
        fail(422,'Prompt variable values must be strings')
    output=template.format_map(variables)
    if len(output)>1000000:
        fail(413,'Rendered prompt exceeds one million characters')
    return output
