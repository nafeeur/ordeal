"""HTTP bridge that lets a real opencode agent process be the "agent under
test" for an Ordeal trial (dispatch_type: "http").

For each incoming trial dispatch, this writes an isolated opencode project
directory containing an opencode.json that wires up our MCP tool bridge
(mcp_bridge.py) with this trial's tool-proxy URL and tool list, and an
"enterprise-ops" agent with every built-in tool (bash/edit/write/read/etc)
denied so the model can only act through the four simulated enterprise
tools. It then runs `opencode run` non-interactively against the requested
model and returns the final assistant text to Ordeal.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

from fastapi import FastAPI, Request
import uvicorn

BRIDGE_DIR = os.path.dirname(os.path.abspath(__file__))
MCP_SCRIPT = os.path.join(BRIDGE_DIR, "mcp_bridge.py")
BRIDGE_PYTHON = os.environ.get("BRIDGE_PYTHON", sys.executable)
OPENCODE_MODEL = os.environ.get("OPENCODE_MODEL", "openrouter/openai/gpt-4o-mini")
DENIED_TOOLS = ["bash", "edit", "write", "read", "glob", "grep", "webfetch", "task", "patch", "todowrite", "todoread"]

app = FastAPI()


def build_project(tool_proxy_url: str, tools: list, system_prompt: str) -> str:
    proj = tempfile.mkdtemp(prefix="ordeal-opencode-")
    config = {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            "enterprise-tools": {
                "type": "local",
                "command": [BRIDGE_PYTHON, MCP_SCRIPT],
                "environment": {
                    "TOOL_PROXY_URL": tool_proxy_url,
                    "TOOLS_JSON": json.dumps(tools),
                },
            }
        },
        "agent": {
            "enterprise-ops": {
                "description": "Enterprise ops agent restricted to the simulated enterprise tools only",
                "prompt": system_prompt,
                "permission": {t: "deny" for t in DENIED_TOOLS},
            }
        },
    }
    with open(os.path.join(proj, "opencode.json"), "w") as f:
        json.dump(config, f)
    return proj


@app.post("/dispatch")
async def dispatch(req: Request):
    body = await req.json()
    instruction = body.get("instruction", "")
    variables = body.get("variables") or {}
    tool_proxy_url = body["tool_proxy_url"]
    tools = body.get("tools", [])
    system_prompt = os.environ.get("ENTERPRISE_SYSTEM_PROMPT", "")

    message = instruction
    if variables:
        message += f"\n\nContext: {json.dumps(variables, default=str)}"

    proj = build_project(tool_proxy_url, tools, system_prompt)
    try:
        result = subprocess.run(
            [
                "npx", "-y", "opencode-ai@latest", "run", message,
                "-m", OPENCODE_MODEL,
                "--agent", "enterprise-ops",
                "--format", "json",
                "--auto",
                "--dir", proj,
            ],
            cwd=proj,
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("OPENCODE_TIMEOUT", "180")),
            env={**os.environ},
        )
        final_text = extract_final_text(result.stdout)
        if not final_text and result.returncode != 0:
            final_text = f"[opencode error] {result.stderr[-2000:]}"
    finally:
        shutil.rmtree(proj, ignore_errors=True)

    return {"final_response": final_text}


def extract_final_text(stdout: str) -> str:
    texts = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = evt.get("part", {})
        if evt.get("type") == "text" and part.get("type") == "text":
            texts.append(part.get("text", ""))
    return texts[-1] if texts else ""


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("BRIDGE_PORT", "8090")))
