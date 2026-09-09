"""Minimal hand-rolled MCP stdio server.

Exposes a static set of tools (read from the TOOLS_JSON env var) and forwards
every tools/call to Ordeal's tool-proxy HTTP endpoint (TOOL_PROXY_URL env var,
called as POST {TOOL_PROXY_URL}/{tool_name}). Implements only the handful of
JSON-RPC methods opencode needs as an MCP client: initialize,
notifications/initialized, tools/list, tools/call.
"""
import json
import os
import sys

import httpx

TOOLS = json.loads(os.environ.get("TOOLS_JSON", "[]"))
TOOL_PROXY_URL = os.environ.get("TOOL_PROXY_URL", "").rstrip("/")


def write_message(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def handle_initialize(msg):
    write_message({
        "jsonrpc": "2.0",
        "id": msg["id"],
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ordeal-enterprise-bridge", "version": "1.0.0"},
        },
    })


def handle_tools_list(msg):
    tools = [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "inputSchema": t.get("input_schema") or {"type": "object", "properties": {}},
        }
        for t in TOOLS
    ]
    write_message({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": tools}})


def handle_tools_call(msg):
    params = msg.get("params", {})
    name = params.get("name")
    args = params.get("arguments") or {}
    try:
        r = httpx.post(f"{TOOL_PROXY_URL}/{name}", json=args, timeout=60)
        r.raise_for_status()
        result = r.json()
        is_error = False
    except Exception as e:
        result = {"error": str(e)}
        is_error = True
    write_message({
        "jsonrpc": "2.0",
        "id": msg["id"],
        "result": {
            "content": [{"type": "text", "text": json.dumps(result, default=str)}],
            "isError": is_error,
        },
    })


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        if method == "initialize":
            handle_initialize(msg)
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            handle_tools_list(msg)
        elif method == "tools/call":
            handle_tools_call(msg)
        elif "id" in msg:
            write_message({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"method not found: {method}"}})


if __name__ == "__main__":
    main()
