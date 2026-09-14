from __future__ import annotations
import base64
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import time
import pytest
import sqlalchemy as sa
from cryptography.exceptions import InvalidTag, InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi.testclient import TestClient
from ordeal_platform import db
from ordeal_platform.operations import backup, restore, checkpoint, maintenance
from ordeal_platform.security import authenticate
from ordeal_platform.server import create_app


def test_secrets_and_artifacts_encrypted_and_bound(platform):
    p = platform
    secret = p.call('POST', f'/api/v1/projects/{p.project}/secrets', expected=201,
                    json={'name': 'provider.key', 'value': 'never-in-database-plaintext'})
    assert 'value' not in secret and 'ciphertext' not in secret
    p.call('POST', f'/api/v1/projects/{p.project}/secrets', expected=201,
           json={'name': 'provider.key', 'value': 'rotated-secret'})
    assert p.call('GET', f'/api/v1/projects/{p.project}/secrets')['items'][0]['name'] == 'provider.key'
    plain = b'sensitive artifact contents'
    artifact = p.call('POST', f'/api/v1/projects/{p.project}/artifacts', expected=201, content=plain)
    downloaded = p.call('GET', f'/api/v1/artifacts/{artifact["id"]}')
    assert downloaded.content == plain
    for file in (p.settings.data_dir / 'artifacts').rglob('*'):
        if file.is_file():
            assert plain not in file.read_bytes()
    with p.database.tx() as conn:
        secret_row = db.row(conn, sa.select(db.secrets))
        assert 'rotated-secret' not in secret_row['ciphertext']
        with pytest.raises(InvalidTag):
            p.app.state.vault.decrypt(secret_row['ciphertext'], 'wrong-tenant-context')


def test_signed_audit_checkpoint_and_tamper_detection(platform):
    p = platform
    p.trace()
    exported = p.call('GET', '/api/v1/audit/checkpoint')
    payload = exported['checkpoint']
    assert payload['valid'] is True
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(exported['public_key']))
    key.verify(base64.b64decode(exported['signature']), db.canonical(payload).encode())
    altered = dict(payload, tenant_id='not-the-tenant')
    with pytest.raises(InvalidSignature):
        key.verify(base64.b64decode(exported['signature']), db.canonical(altered).encode())
    with p.database.tx() as conn:
        first = db.row(conn, db.scoped(db.audit, p.owner['tenant_id']).order_by(db.audit.c.sequence))
        conn.execute(db.audit.update().where(db.audit.c.id == first['id']).values(details={'tampered': True}))
    assert p.call('GET', '/api/v1/audit/checkpoint')['checkpoint']['valid'] is False


def test_retention_holds_and_shared_artifact_references(platform):
    p = platform
    old = p.trace(name='delete-me')
    held = p.trace(name='hold-me')
    p.call('POST', f'/api/v1/traces/{held["id"]}/legal-hold')
    p.call('DELETE', f'/api/v1/traces/{held["id"]}', expected=409)
    a = p.call('POST', f'/api/v1/projects/{p.project}/artifacts', expected=201, content=b'shared')
    b = p.call('POST', f'/api/v1/projects/{p.project}/artifacts', expected=201, content=b'shared')
    p.call('POST', f'/api/v1/artifacts/{b["id"]}/legal-hold')
    with p.database.tx() as conn:
        conn.execute(db.traces.update().where(db.traces.c.tenant_id == p.owner['tenant_id']).values(created_at=time.time() - 400 * 86400))
        conn.execute(db.artifacts.update().where(db.artifacts.c.tenant_id == p.owner['tenant_id']).values(created_at=time.time() - 400 * 86400))
    result = p.call('POST', '/api/v1/maintenance')
    assert result['traces_deleted'] == 1 and result['artifacts_deleted'] == 1
    p.call('GET', f'/api/v1/traces/{old["id"]}', expected=404)
    p.call('GET', f'/api/v1/traces/{held["id"]}')
    assert p.call('GET', f'/api/v1/artifacts/{b["id"]}').content == b'shared'


def test_monitor_dedup_acknowledge_and_resolution(platform):
    p = platform
    monitor = p.resource('monitor', 'low-pass-rate', {'metric': 'pass_rate', 'operator': 'lt', 'threshold': .9,
        'minimum_samples': 1, 'window_seconds': 3600})
    p.trace(status='fail')
    assert p.call('POST', '/api/v1/maintenance')['alerts_created'] == 1
    assert p.call('POST', '/api/v1/maintenance')['alerts_created'] == 0
    alert = p.call('GET', f'/api/v1/projects/{p.project}/alerts')['items'][0]
    p.call('POST', f'/api/v1/alerts/{alert["id"]}/acknowledge')
    assert p.call('POST', '/api/v1/maintenance')['alerts_created'] == 0
    p.call('POST', f'/api/v1/resources/{monitor["id"]}/versions', expected=201,
           json={'base_version': 1, 'data': {'metric': 'pass_rate', 'operator': 'lt', 'threshold': 0, 'minimum_samples': 1}})
    assert p.call('POST', '/api/v1/maintenance')['alerts_resolved'] == 1


def test_cache_expiry_and_draft_billing_idempotence(platform):
    p = platform
    cached = p.call('POST', f'/api/v1/projects/{p.project}/cache', json={'namespace': 'judge', 'key': {'model': 'test', 'input': 'a'},
        'value': {'score': 1}, 'ttl_seconds': 60})
    assert p.call('GET', f'/api/v1/projects/{p.project}/cache/{cached["key"]}')['value'] == {'score': 1}
    with p.database.tx() as conn:
        conn.execute(db.cache.update().where(db.cache.c.tenant_id == p.owner['tenant_id']).values(expires_at=0))
    p.call('GET', f'/api/v1/projects/{p.project}/cache/{cached["key"]}', expected=404)
    p.trace()
    body = {'period': datetime.now(timezone.utc).strftime('%Y-%m'), 'rates_microusd': {'traces': 100}}
    invoice = p.call('POST', '/api/v1/billing/invoices', expected=201, json=body)
    assert invoice['status'] == 'draft' and invoice['data']['total_microusd'] == 100
    assert p.call('POST', '/api/v1/billing/invoices', expected=201, json=body)['id'] == invoice['id']
    p.call('POST', '/api/v1/billing/invoices', expected=409,
           json={**body, 'rates_microusd': {'traces': 200}})


def test_encrypted_backup_restore_end_to_end(platform, tmp_path):
    p = platform
    trace = p.trace(name='restored-run')
    artifact = p.call('POST', f'/api/v1/projects/{p.project}/artifacts', expected=201, content=b'backup-sensitive-contents')
    archive = tmp_path / 'snapshot.ordeal'
    result = backup(p.settings, archive)
    assert result['encrypted'] is True and result['files'] == 2
    assert b'backup-sensitive-contents' not in archive.read_bytes()
    destination = tmp_path / 'restored'
    restore(archive, destination, p.settings.master_key)
    settings = replace(p.settings, data_dir=destination, database_url=f'sqlite:///{destination}/platform.db')
    database = db.Database(settings.database_url)
    app = create_app(settings, database)
    with TestClient(app) as client:
        assert client.get(f'/api/v1/traces/{trace["id"]}', headers=p.headers).json()['name'] == 'restored-run'
        assert client.get(f'/api/v1/artifacts/{artifact["id"]}', headers=p.headers).content == b'backup-sensitive-contents'
        assert client.get('/api/v1/audit/checkpoint', headers=p.headers).json()['checkpoint']['valid'] is True
    database.engine.dispose()
    corrupted = tmp_path / 'corrupt.ordeal'
    data = bytearray(archive.read_bytes()); data[-1] ^= 1; corrupted.write_bytes(data)
    with pytest.raises(InvalidTag):
        restore(corrupted, tmp_path / 'bad-restore', p.settings.master_key)
    with pytest.raises(ValueError):
        restore(archive, destination, p.settings.master_key)


def test_evaluation_monitor_uses_pinned_results_not_producer_status(platform):
    p=platform
    ev=p.resource('evaluator','no-refund',{'type':'tool_not_called','tool':'refund'})
    p.resource('monitor','real-evaluation',{'metric':'evaluation_failure_rate','operator':'gt','threshold':.1,
        'minimum_samples':1,'evaluator_id':ev['id'],'evaluator_version':1})
    trace=p.trace(status='pass')
    assert p.call('POST','/api/v1/maintenance')['alerts_created']==0
    result=p.call('POST',f'/api/v1/traces/{trace["id"]}/evaluate',json={'evaluator_id':ev['id'],'version':1})
    assert result['result']['verdict']=='fail'
    assert p.call('POST','/api/v1/maintenance')['alerts_created']==1


def test_monitor_minimum_counts_scored_runs_not_unscored_runs(platform):
    p=platform
    p.resource('monitor','minimum',{'metric':'pass_rate','operator':'lt','threshold':.9,'minimum_samples':2})
    p.trace(status='fail');p.trace(status='unset')
    assert p.call('POST','/api/v1/maintenance')['alerts_created']==0
    p.trace(status='pass')
    assert p.call('POST','/api/v1/maintenance')['alerts_created']==1


def test_ci_status_unknown_never_green():
    from ordeal_platform.operations import connector_request
    for kind in ['github_status','gitlab_status']:
        config={'type':kind,'url':'https://example.test/{sha}','git_sha':'a'*40}
        _,_,body=connector_request(config,{'id':'e','type':'experiment.finished','data':{}},'key')
        assert json.loads(body)['state']=='pending'
        _,_,body=connector_request(config,{'id':'e','type':'experiment.finished','data':{'passed':False}},'key')
        assert json.loads(body)['state'] in {'failed','failure'}


def test_uploaded_report_triggers_one_level_of_completion_automation(platform):
    from ordeal_platform.runner import execute,RunnerPolicy
    from test_jobs_and_runner import queue,register,claim,ROOT
    p=platform
    p.resource('automation','followup',{'event':'experiment.finished','job':{'kind':'suite','payload':{'suite_path':'examples/customer_support/suite.py'}}})
    report=execute(RunnerPolicy(ROOT,['examples/customer_support/suite.py'],mode='trusted-process'),'examples/customer_support/suite.py')
    p.resource('experiment','uploaded',{'report':report})
    jobs=p.call('GET',f'/api/v1/projects/{p.project}/jobs')['items']
    assert len(jobs)==1 and jobs[0]['payload']['_automation_depth']==1
    runner=register(p);leased=claim(p,runner)
    p.call('POST',f'/api/v1/jobs/{leased["id"]}/complete',json={'runner_id':runner['id'],'lease_token':leased['lease_token'],'result':{'report':report}})
    assert p.call('GET',f'/api/v1/projects/{p.project}/jobs')['total']==1
