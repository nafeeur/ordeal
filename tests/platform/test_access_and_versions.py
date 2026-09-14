from __future__ import annotations
import time
import pytest
import sqlalchemy as sa
from ordeal_platform import db


def test_health_and_cookie_csrf(platform):
    p = platform
    assert p.client.get('/healthz').status_code == 200
    assert p.client.get('/readyz').status_code == 200
    p.call('GET', '/api/v1/me', headers={}, expected=401)
    p.call('POST', '/auth/session', headers={}, json={'token': p.owner['token']})
    p.call('GET', '/api/v1/me', headers={})
    p.call('POST', '/api/v1/projects', headers={}, json={'name': 'No origin'}, expected=403)
    p.call('POST', '/api/v1/projects', headers={'Origin': 'http://evil.example'}, json={'name': 'Bad origin'}, expected=403)
    p.call('POST', '/api/v1/projects', headers={'Origin': 'http://testserver'}, json={'name': 'Allowed'}, expected=201)
    response = p.client.get('/')
    assert "script-src 'self'" in response.headers['content-security-policy']
    assert response.headers['x-content-type-options'] == 'nosniff'
    p.call('POST', '/auth/logout', headers={'Origin': 'http://testserver'})
    p.call('GET', '/api/v1/me', headers={}, expected=401)


def test_tenant_boundary_every_resource(platform):
    p = platform
    dataset = p.resource('dataset', 'private', {'cases': []})
    trace = p.trace()
    job = p.call('POST', f'/api/v1/projects/{p.project}/jobs', expected=201, json={'payload': {'suite_path': 'suite.py'}})
    artifact = p.call('POST', f'/api/v1/projects/{p.project}/artifacts', expected=201, content=b'private payload')
    paths = [f'/api/v1/resources/{dataset["id"]}', f'/api/v1/resources/{dataset["id"]}/versions',
             f'/api/v1/traces/{trace["id"]}', f'/api/v1/traces/{trace["id"]}/export',
             f'/api/v1/jobs/{job["id"]}', f'/api/v1/artifacts/{artifact["id"]}',
             f'/api/v1/projects/{p.project}/resources', f'/api/v1/projects/{p.project}/traces',
             f'/api/v1/projects/{p.project}/analytics', f'/api/v1/projects/{p.project}/jobs']
    for path in paths:
        p.call('GET', path, headers=p.other_headers, expected=404)
    assert all(x['tenant_id'] == p.other['tenant_id'] for x in p.call('GET', '/api/v1/audit', headers=p.other_headers)['items'])
    p.call('DELETE', f'/api/v1/resources/{dataset["id"]}', headers=p.other_headers, expected=404)
    p.call('POST', f'/api/v1/traces/{trace["id"]}/legal-hold', headers=p.other_headers, expected=404)


def test_rbac_scope_revocation_and_owner_protection(platform):
    p = platform
    second = p.call('POST', '/api/v1/projects', expected=201, json={'name': 'Restricted'})
    principal = p.call('POST', '/api/v1/principals', expected=201,
                       json={'name': 'reader', 'role': 'viewer', 'projects': [p.project]})
    h = {'Authorization': 'Bearer ' + principal['token']}
    projects = p.call('GET', '/api/v1/projects', headers=h)['items']
    assert [x['id'] for x in projects] == [p.project]
    p.call('GET', f'/api/v1/projects/{second["id"]}/resources', headers=h, expected=404)
    p.call('POST', f'/api/v1/projects/{p.project}/resources', headers=h, expected=403,
           json={'kind': 'dataset', 'name': 'bad', 'data': {'cases': []}})
    p.call('GET', '/api/v1/audit', headers=h, expected=403)
    p.call('GET', '/api/v1/principals', headers=h, expected=403)
    p.call('PATCH', f'/api/v1/principals/{p.owner["principal_id"]}', json={'active': False}, expected=409)
    p.call('PATCH', f'/api/v1/principals/{principal["id"]}', json={'active': False})
    p.call('GET', '/api/v1/me', headers=h, expected=401)


def test_service_account_restricted_permissions(platform):
    p = platform
    service = p.call('POST', '/api/v1/principals', expected=201,
                     json={'name': 'ingester', 'role': 'developer', 'permissions': ['write'], 'projects': [p.project]})
    h = {'Authorization': 'Bearer ' + service['token']}
    p.call('GET', f'/api/v1/projects/{p.project}/traces', headers=h, expected=403)
    from conftest import sample_trace
    p.call('POST', f'/api/v1/projects/{p.project}/traces', headers=h, json=sample_trace())
    p.call('POST', '/api/v1/principals', headers=h, expected=403, json={'name': 'escalate', 'role': 'admin'})


def test_token_expiry_and_rotation(platform):
    p = platform
    created = p.call('POST', f'/api/v1/principals/{p.owner["principal_id"]}/tokens', expected=201, json={'label': 'rotating', 'days': 1})
    h = {'Authorization': 'Bearer ' + created['token']}
    p.call('GET', '/api/v1/me', headers=h)
    p.call('DELETE', f'/api/v1/tokens/{created["id"]}')
    p.call('GET', '/api/v1/me', headers=h, expected=401)
    with p.database.tx() as conn:
        conn.execute(db.tokens.update().values(expires_at=time.time() - 10))
    p.call('GET', '/api/v1/me', expected=401)


def test_immutable_versions_cas_and_promotion(platform):
    p = platform
    resource = p.resource('prompt', 'support', {'template': 'Hello {name}', 'variables': ['name']})
    rid = resource['id']
    v2 = p.call('POST', f'/api/v1/resources/{rid}/versions', expected=201,
                json={'base_version': 1, 'data': {'template': 'Welcome {name}', 'variables': ['name']}})
    assert v2['version'] == 2
    assert p.call('GET', f'/api/v1/resources/{rid}?version=1')['data']['template'] == 'Hello {name}'
    p.call('POST', f'/api/v1/resources/{rid}/versions', expected=409, json={'base_version': 1, 'data': {'template': 'Lost update'}})
    p.call('POST', f'/api/v1/resources/{rid}/promote', json={'environment': 'production', 'version': 1})
    latest = p.call('GET', f'/api/v1/resources/{rid}')
    assert latest['aliases']['production'] == 1
    assert latest['head'] == 2
    p.call('POST', f'/api/v1/resources/{rid}/promote', expected=404, json={'environment': 'production', 'version': 999})
    assert p.call('GET', f'/api/v1/resources/{rid}/diff?before=1&after=2')
    p.call('DELETE', f'/api/v1/resources/{rid}')
    p.call('GET', f'/api/v1/resources/{rid}', expected=404)


@pytest.mark.parametrize('data', [{'cases': [{'id': 'a'}, {'id': 'a'}]}, {'cases': [{}]}, {'cases': 'bad'}, {'cases': [None]}])
def test_dataset_schema_rejects_invalid_cases(platform, data):
    p = platform
    p.call('POST', f'/api/v1/projects/{p.project}/resources', expected=422,
           json={'kind': 'dataset', 'name': 'bad', 'data': data})
