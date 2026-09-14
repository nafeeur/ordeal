from __future__ import annotations

from typing import Any

from ..mcp import create_mcp_server
from ..world import World


def server(world: World, **kwargs: Any) -> Any:
    return create_mcp_server(world, **kwargs)
