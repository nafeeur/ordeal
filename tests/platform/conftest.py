from __future__ import annotations
import base64
from copy import deepcopy
from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from ordeal_platform import db, services
from ordeal_platform.config import Settings
from ordeal_platform.server import create_app

@pytest.fixture
def platform(tmp_path):
    root = tmp_path / 'data'
    root.mkdir()
    settings = Settings(data_dir=root, database_url=f'sqlite:///{root}/platform.db',
                        master_key=base64.urlsafe_b64encode(os.urandom(32)).decode(),
                        public_url='http://testserver', request_limit=100000,
                        outbound_hosts=('127.0.0.1', 'localhost'), allow_private_outbound=True)
    database = db.Database(settings.database_url)
    database.migrate()
    owner = services.bootstrap(database, 'Acme')
    other = services.bootstrap(database, 'Other')
    app = create_app(settings, database)
    with TestClient(app) as client:
        p = SimpleNamespace(settings=settings, database=database, owner=owner, other=other,
                            app=app, client=client, project=owner['project_id'])
        p.headers = {'Authorization': 'Bearer ' + owner['token']}
        p.other_headers = {'Authorization': 'Bearer ' + other['token']}
        def call(method, path, *, expected=200, headers=None, **kwargs):
            response = client.request(method, path, headers=p.headers if headers is None else headers, **kwargs)
            assert response.status_code == expected, (response.status_code, response.text)
            return response.json() if response.content and 'json' in response.headers.get('content-type', '') else response
        p.call = call
        p.resource = lambda kind, name, data: call('POST', f'/api/v1/projects/{p.project}/resources', expected=201,
                                                   json={'kind': kind, 'name': name, 'data': data})
        p.trace = lambda **overrides: call('POST', f'/api/v1/projects/{p.project}/traces', json=sample_trace(**overrides))
        yield p
    database.engine.dispose()


def sample_trace(**overrides):
    result = {
        'name': 'refund-order', 'status': 'pass', 'duration_ms': 42,
        'agent_version': 'v1', 'usage': {'total_tokens': 100, 'cost_usd': .001},
        'trajectory': {
            'initial_state': {'refunded': False}, 'final_state': {'refunded': True},
            'final_output': 'Refund completed',
            'events': [
                {'kind': 'tool_call', 'name': 'authorize', 'payload': {'call_id': 'a', 'arguments': {'order': 'A100'}}, 'timestamp': 1},
                {'kind': 'tool_result', 'name': 'authorize', 'payload': {'call_id': 'a', 'result': {'allowed': True}}, 'timestamp': 2},
                {'kind': 'tool_call', 'name': 'refund', 'payload': {'call_id': 'b', 'arguments': {'order': 'A100'}}, 'timestamp': 3},
                {'kind': 'tool_result', 'name': 'refund', 'payload': {'call_id': 'b', 'result': {'ok': True}}, 'timestamp': 4},
            ]},
    }
    result.update(deepcopy(overrides))
    return result

@pytest.fixture
def live_server(platform):
    """Real TCP HTTP service, separate from the in-process TestClient."""
    import socket
    import threading
    import time
    import uvicorn
    import httpx
    p = platform
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    url = f'http://127.0.0.1:{sock.getsockname()[1]}'
    settings = replace(p.settings, public_url=url)
    app = create_app(settings, p.database)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(.01)
    assert server.started, 'HTTP service failed to start'
    yield SimpleNamespace(url=url, settings=settings, app=app, server=server, platform=p)
    server.should_exit = True
    thread.join(timeout=5)
    sock.close()
    assert not thread.is_alive(), 'HTTP service failed to stop'
