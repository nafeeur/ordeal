import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import pytest

from ordeal_agent import OutputContains, CommandAgent, HTTPAgent, Runner, Scenario, Verdict, World


@pytest.mark.asyncio
async def test_command_agent_end_to_end(tmp_path):
    script = tmp_path / "agent.py"
    script.write_text(
        "import json,sys\n"
        "request=json.load(sys.stdin)\n"
        "print(json.dumps({'output':'echo:'+request['instruction'],'metadata':{'usage':{'total_tokens':3}}}))\n"
    )
    agent = CommandAgent(["python", str(script)], name="cmd", version="1")
    result = await Runner().run_scenario(agent, Scenario("cmd", "hello", World("w"), assertions=[OutputContains("echo:hello")]))
    assert result.verdict == Verdict.PASS
    assert result.trajectory.final_output == "echo:hello"
    assert result.usage.total_tokens == 3


@pytest.mark.asyncio
async def test_http_agent_end_to_end():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length))
            payload = json.dumps({"output": "echo:" + request["instruction"], "metadata": {"usage": {"total_tokens": 2}}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        agent = HTTPAgent(f"http://{host}:{port}/agent", name="http", version="1")
        result = await Runner().run_scenario(agent, Scenario("http", "hello", World("w"), assertions=[OutputContains("echo:hello")]))
        assert result.verdict == Verdict.PASS
        assert result.trajectory.final_output == "echo:hello"
        assert result.usage.total_tokens == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
