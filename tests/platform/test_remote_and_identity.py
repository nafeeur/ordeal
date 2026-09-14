from __future__ import annotations
import base64
from dataclasses import replace
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from ordeal_platform import db
from ordeal_platform.evaluators import evaluate
from ordeal_platform.network import NetworkDenied, request as outbound
from ordeal_platform.operations import deliver_one, connector_request
from ordeal_platform.security import authenticate
from conftest import sample_trace


@pytest.fixture
def remote():
    state = SimpleNamespace(requests=[], status=200, claims={}, signing_key=rsa.generate_private_key(public_exponent=65537, key_size=2048))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.respond()
        def do_POST(self):
            self.respond()
        def respond(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            state.requests.append((self.path, dict(self.headers), body))
            status = state.status
            response = {'ok': True}
            if self.path == '/judge':
                response = {'choices': [{'message': {'content': '{"score":0.9,"reason":"Matches reference"}'}}]}
            elif self.path == '/embedding':
                response = {'data': [{'index': 0, 'embedding': [1., 0., 1.]}, {'index': 1, 'embedding': [1., 0., 1.]}]}
            elif self.path == '/eval':
                response = {'verdict': 'pass', 'score': 1, 'message': 'Test endpoint'}
            elif self.path == '/bad-eval':
                response = {'verdict': 'magic', 'score': 100}
            elif self.path == '/token':
                form = parse_qs(body.decode())
                verifier = form['code_verifier'][0]
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                if challenge != state.challenge:
                    status = 400
                else:
                    claims = {'iss': state.url, 'sub': 'user-1', 'aud': 'client-1', 'iat': int(time.time()),
                              'exp': int(time.time()) + 300, 'nonce': state.nonce,
                              'email': 'engineer@example.test', 'email_verified': True, **state.claims}
                    response = {'id_token': jwt.encode(claims, state.signing_key, algorithm='RS256', headers={'kid': 'test-key'})}
            elif self.path == '/jwks':
                jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(state.signing_key.public_key()))
                response = {'keys': [{**jwk, 'kid': 'test-key', 'use': 'sig'}]}
            if self.path == '/redirect':
                status = 302
            encoded = json.dumps(response).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            if status == 302:
                self.send_header('Location', 'http://169.254.169.254/latest/meta-data/')
            self.end_headers()
            self.wfile.write(encoded)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    state.url = f'http://127.0.0.1:{server.server_port}'
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield state
    server.shutdown(); server.server_close(); thread.join(2)


@pytest.mark.parametrize('kind,path,extra', [
    ('http', '/eval', {}),
    ('llm_judge', '/judge', {'model': 'test-model', 'rubric': 'Correctness'}),
    ('embedding_similarity', '/embedding', {'model': 'test-embedding', 'reference': 'Refund completed'}),
])
def test_real_http_evaluator_protocols(platform, remote, kind, path, extra):
    spec = {'type': kind, 'endpoint': remote.url + path, **extra}
    result = evaluate(spec, sample_trace(), settings=platform.settings)
    assert result['verdict'] == 'pass', result
    assert len(remote.requests) == 1


def test_bad_remote_evaluator_and_network_fail_closed(platform, remote):
    p = platform
    bad = evaluate({'type': 'http', 'endpoint': remote.url + '/bad-eval'}, sample_trace(), settings=p.settings)
    assert bad['verdict'] == 'error'
    for url in ['http://169.254.169.254/latest/meta-data', 'http://unlisted.test', remote.url + '/redirect']:
        with pytest.raises(NetworkDenied):
            outbound(p.settings, 'GET', url)
    prod = replace(p.settings, allow_private_outbound=False, outbound_hosts=('127.0.0.1',))
    with pytest.raises(NetworkDenied):
        outbound(prod, 'GET', 'https://127.0.0.1')
    with pytest.raises(NetworkDenied):
        outbound(p.settings, 'GET', remote.url, headers={'X-Test': 'safe\r\nInjected: bad'})


def test_dns_rebinding_validation_before_connect(platform, monkeypatch):
    import socket
    import ordeal_platform.network as net
    settings = replace(platform.settings, outbound_hosts=('public.test',), allow_private_outbound=False)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(NetworkDenied):
        outbound(settings, 'GET', 'https://public.test')
    called = []
    class Connection:
        def __init__(self, hostname, ip, port, timeout):
            called.append((hostname, ip))
        def request(self, *a, **k): pass
        def getresponse(self): return SimpleNamespace(status=200, read=lambda n: b'{}', getheaders=lambda: [])
        def close(self): pass
    monkeypatch.setattr(net, 'PinnedHTTPSConnection', Connection)
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))])
    outbound(settings, 'GET', 'https://public.test')
    assert called == [('public.test', '8.8.8.8')]


def test_webhook_signature_retry_and_dead_letter(platform, remote):
    p = platform
    p.call('POST', f'/api/v1/projects/{p.project}/secrets', expected=201,
           json={'name': 'webhook-key', 'value': 'signing-secret'})
    c = p.resource('connector', 'test-webhook', {'type': 'webhook', 'url': remote.url + '/hook',
        'events': ['trace.failed'], 'secret_name': 'webhook-key'})
    p.trace(status='fail')
    ident = authenticate(p.database, p.owner['token'])
    remote.status = 503
    result = deliver_one(p.database, p.settings, ident)
    assert result['state'] == 'pending' and result['attempt'] == 1
    path, headers, body = remote.requests[0]
    expected = 'sha256=' + hmac.new(b'signing-secret', headers['X-Ordeal-Timestamp'].encode() + b'.' + body, hashlib.sha256).hexdigest()
    assert headers['X-Ordeal-Signature'] == expected
    with p.database.tx() as conn:
        conn.execute(db.deliveries.update().values(not_before=0))
    remote.status = 200
    assert deliver_one(p.database, p.settings, ident)['state'] == 'delivered'
    assert deliver_one(p.database, p.settings, ident) is None
    p.trace(status='fail')
    remote.status = 400
    assert deliver_one(p.database, p.settings, ident)['state'] == 'dead'
    p.trace(status='fail')
    p.call('DELETE', f'/api/v1/resources/{c["id"]}')
    assert deliver_one(p.database, p.settings, ident)['state'] == 'dead'


@pytest.mark.parametrize('kind,field', [('slack', 'text'), ('teams', 'summary'), ('pagerduty', 'routing_key'),
    ('github_status', 'state'), ('github_issue', 'body'), ('gitlab_issue', 'description'),
    ('jira', 'fields'), ('linear', 'query'), ('servicenow', 'short_description')])
def test_provider_connector_payload_contracts(kind, field):
    cfg = {'type': kind, 'url': 'https://example.test/hooks/{sha}', 'project_key': 'AI', 'team_id': 'team'}
    envelope = {'id': 'event-1', 'type': 'experiment.finished', 'data': {'passed': False, 'git_sha': 'a' * 40}}
    url, headers, body = connector_request(cfg, envelope, 'test-secret')
    assert field in json.loads(body)
    if kind == 'github_status':
        assert json.loads(body)['state'] == 'failure' and url.endswith('a' * 40)


def configure_oidc(p, remote):
    p.settings.oidc.update({'issuer': remote.url, 'client_id': 'client-1', 'tenant_id': p.owner['tenant_id'],
        'authorization_endpoint': remote.url + '/authorize', 'token_endpoint': remote.url + '/token',
        'jwks_uri': remote.url + '/jwks', 'group_roles': {'Developers': 'developer'}})
    user = p.call('POST', '/scim/v2/Users', expected=201, json={'userName': 'engineer@example.test', 'displayName': 'Engineer'})
    return user


def start_oidc(p, remote):
    response = p.client.get('/auth/oidc/start', follow_redirects=False)
    assert response.status_code == 307
    query = parse_qs(urlsplit(response.headers['location']).query)
    remote.nonce, remote.challenge = query['nonce'][0], query['code_challenge'][0]
    return query['state'][0]


def test_oidc_pkce_signature_nonce_session_and_scim_deactivation(platform, remote):
    p = platform
    user = configure_oidc(p, remote)
    group = p.call('POST', '/scim/v2/Groups', expected=201, json={'displayName': 'Developers', 'members': [{'value': user['id']}]})
    state = start_oidc(p, remote)
    response = p.client.get('/auth/oidc/callback', params={'state': state, 'code': 'valid'}, follow_redirects=False)
    assert response.status_code == 303, response.text
    me = p.call('GET', '/api/v1/me', headers={})
    assert me['role'] == 'developer'
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'ordeal_oidc_state' not in p.client.cookies
    again = p.client.get('/auth/oidc/callback', params={'state': state, 'code': 'valid'}, follow_redirects=False)
    assert again.status_code == 400
    p.call('PATCH', f'/scim/v2/Groups/{group["id"]}', json={'Operations': [{'op': 'remove', 'path': f'members[value eq "{user["id"]}"]'}]})
    assert p.call('GET', '/api/v1/me', headers={})['role'] == 'viewer'
    p.call('PATCH', f'/scim/v2/Users/{user["id"]}', json={'Operations': [{'op': 'replace', 'path': 'active', 'value': False}]})
    p.call('GET', '/api/v1/me', headers={}, expected=401)
    p.call('PATCH', f'/scim/v2/Users/{p.owner["principal_id"]}', expected=404,
           json={'Operations': [{'op': 'replace', 'path': 'active', 'value': False}]})


@pytest.mark.parametrize('claims', [{'aud': 'wrong-client'}, {'iss': 'wrong-issuer'}, {'nonce': 'wrong-nonce'},
    {'email_verified': False}, {'exp': 1}, {'azp': 'other-client'}])
def test_oidc_invalid_identity_rejected(platform, remote, claims):
    p = platform
    configure_oidc(p, remote)
    state = start_oidc(p, remote)
    remote.claims = claims
    response = p.client.get('/auth/oidc/callback', params={'state': state, 'code': 'valid'}, follow_redirects=False)
    assert response.status_code == 401, response.text
    assert 'ordeal_session' not in p.client.cookies


def test_oidc_state_bound_to_browser(platform, remote):
    p = platform
    configure_oidc(p, remote)
    state = start_oidc(p, remote)
    with TestClient(p.app) as other_browser:
        response = other_browser.get('/auth/oidc/callback', params={'state': state, 'code': 'valid'}, follow_redirects=False)
    assert response.status_code == 400
    assert not any(x[0] == '/token' for x in remote.requests)


def test_scim_filters_and_tenant_isolation(platform):
    p = platform
    u = p.call('POST', '/scim/v2/Users', expected=201, json={'userName': 'someone@example.test', 'externalId': 'external-1'})
    p.call('GET', f'/scim/v2/Users/{u["id"]}', headers=p.other_headers, expected=404)
    result = p.call('GET', '/scim/v2/Users', params={'filter': 'userName eq "someone@example.test"'})
    assert result['totalResults'] == 1
    p.call('GET', '/scim/v2/Users', params={'filter': 'userName pr'}, expected=400)
    p.call('POST', '/scim/v2/Users', expected=409, json={'userName': 'someone@example.test'})
