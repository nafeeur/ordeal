from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import threading
import time
import pytest
from ordeal_platform import db
from ordeal_platform.runner import RunnerPolicy, ExecutionFailure, execute, bounded_execute, internal_work_once
from ordeal_platform.security import authenticate

ROOT = Path(__file__).resolve().parents[2]


def queue(p, **overrides):
    body = {'payload': {'suite_path': 'examples/customer_support/suite.py'}}
    body.update(overrides)
    return p.call('POST', f'/api/v1/projects/{p.project}/jobs', expected=201, json=body)


def register(p, labels=None):
    return p.call('POST', '/api/v1/runners', expected=201, json={'name': 'test-runner', 'labels': labels or []})


def claim(p, r):
    return p.call('POST', '/api/v1/jobs/claim', json={'runner_id': r['id']})['job']


def test_job_idempotency_labels_priority_and_foreign_lease(platform):
    p = platform
    a = queue(p, idempotency_key='stable', labels=['private'])
    assert queue(p, idempotency_key='stable', labels=['private'])['id'] == a['id']
    p.call('POST', f'/api/v1/projects/{p.project}/jobs', expected=409,
           json={'idempotency_key': 'stable', 'payload': {'suite_path': 'other.py'}})
    r = register(p)
    assert claim(p, r) is None
    r2 = register(p, ['private'])
    claimed = claim(p, r2)
    assert claimed['id'] == a['id']
    p.call('POST', f'/api/v1/jobs/{a["id"]}/heartbeat', expected=409,
           json={'runner_id': r['id'], 'lease_token': claimed['lease_token']})


def test_simultaneous_claims_are_unique(platform):
    p = platform
    for i in range(12):
        queue(p)
    runners = [register(p) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        claimed = list(pool.map(lambda r: claim(p, r), runners))
    assert len({x['id'] for x in claimed}) == 12
    assert claim(p, runners[0]) is None


def test_stale_lease_fenced_and_cancellation(platform):
    p = platform
    j = queue(p)
    r1, r2 = register(p), register(p)
    a = claim(p, r1)
    with p.database.tx() as conn:
        conn.execute(db.jobs.update().where(db.jobs.c.id == j['id']).values(lease_until=time.time() - 1))
    b = claim(p, r2)
    assert b['attempt'] == 2 and a['lease_token'] != b['lease_token']
    p.call('POST', f'/api/v1/jobs/{j["id"]}/heartbeat', expected=409,
           json={'runner_id': r1['id'], 'lease_token': a['lease_token']})
    p.call('POST', f'/api/v1/jobs/{j["id"]}/cancel')
    p.call('POST', f'/api/v1/jobs/{j["id"]}/heartbeat', expected=409,
           json={'runner_id': r2['id'], 'lease_token': b['lease_token']})
    assert claim(p, r2) is None


def test_actual_suite_child_and_completion_idempotence(platform):
    p = platform
    report = execute(RunnerPolicy(ROOT, ['examples/customer_support/suite.py'], mode='trusted-process'), 'examples/customer_support/suite.py')
    assert len(report['results']) == 2
    assert all(x['verdict'] == 'pass' for x in report['results'])
    j = queue(p)
    r = register(p)
    leased = claim(p, r)
    completion = {'runner_id': r['id'], 'lease_token': leased['lease_token'], 'result': {'report': report}}
    assert p.call('POST', f'/api/v1/jobs/{j["id"]}/complete', json=completion)['state'] == 'succeeded'
    p.call('POST', f'/api/v1/jobs/{j["id"]}/complete', json=completion)
    assert p.call('GET', f'/api/v1/projects/{p.project}/resources?kind=experiment')['total'] == 1
    assert p.call('GET', f'/api/v1/projects/{p.project}/traces')['total'] == 2
    completion['actual_cost_usd'] = 1
    p.call('POST', f'/api/v1/jobs/{j["id"]}/complete', json=completion, expected=409)


def test_budget_rejects_overreservation(platform):
    p = platform
    p.call('PUT', f'/api/v1/projects/{p.project}/settings', json={'limits': {'jobs_daily': 2, 'cost_microusd_daily': 1000000}})
    queue(p, estimated_cost_usd=.75)
    p.call('POST', f'/api/v1/projects/{p.project}/jobs', expected=429,
           json={'payload': {'suite_path': 'test.py'}, 'estimated_cost_usd': .5})
    queue(p, estimated_cost_usd=.25)
    p.call('POST', f'/api/v1/projects/{p.project}/jobs', expected=429,
           json={'payload': {'suite_path': 'test.py'}})


def test_online_evaluation_for_write_only_ingester(platform):
    p = platform
    evaluator = p.resource('evaluator', 'auth-before-refund', {'type': 'tool_order', 'before': 'authorize', 'after': 'refund'})
    p.call('PUT', f'/api/v1/projects/{p.project}/settings', json={'online_evaluators': [{'id': evaluator['id'], 'version': 1, 'sample_rate': 1}]})
    ingester = p.call('POST', '/api/v1/principals', expected=201,
                     json={'name': 'producer', 'role': 'developer', 'permissions': ['write'], 'projects': [p.project]})
    from conftest import sample_trace
    p.call('POST', f'/api/v1/projects/{p.project}/traces', headers={'Authorization': 'Bearer ' + ingester['token']}, json=sample_trace())
    ident = authenticate(p.database, p.owner['token'])
    result = internal_work_once(p.database, p.settings, ident)
    assert result['state'] == 'succeeded'
    assert internal_work_once(p.database, p.settings, ident) is None
    jobs = p.call('GET', f'/api/v1/projects/{p.project}/jobs')['items']
    assert len(jobs) == 1
    assert jobs[0]['result']['evaluation']['verdict'] == 'pass'


def test_allowlist_and_docker_policy_are_fail_closed(tmp_path):
    suite = tmp_path / 'suite.py'
    suite.write_text('x = 1')
    with pytest.raises(ValueError):
        RunnerPolicy(tmp_path, [], mode='trusted-process')
    with pytest.raises(ValueError):
        RunnerPolicy(tmp_path, ['suite.py'], mode='docker', image='unsafe:latest')
    policy = RunnerPolicy(tmp_path, ['suite.py'], mode='docker', image='sha256:' + 'a' * 64)
    argv, name = policy.command('suite.py', extra_env={'API_KEY': 'secret-value'})
    assert '--read-only' in argv and '--cap-drop' in argv and '--network' in argv
    assert 'secret-value' not in ' '.join(argv)
    with pytest.raises(ExecutionFailure):
        policy.resolve('../escape.py')
    external = tmp_path.parent / 'external.py'
    external.write_text('x = 2')
    suite.unlink()
    suite.symlink_to(external)
    with pytest.raises(ExecutionFailure):
        policy.resolve('suite.py')


@pytest.mark.parametrize('reason', ['timeout', 'cancel', 'output'])
def test_real_subprocess_limits(tmp_path, reason):
    import sys
    code = 'import time; time.sleep(30)' if reason != 'output' else 'print("x" * 100000)'
    cancelled = threading.Event()
    if reason == 'cancel':
        cancelled.set()
    start = time.monotonic()
    with pytest.raises(ExecutionFailure):
        bounded_execute([sys.executable, '-c', code], env={'PATH': os.environ['PATH']}, cwd=tmp_path,
                        timeout=.15, max_output=4096, cancel=cancelled)
    assert time.monotonic() - start < 5


def test_matching_job_after_large_nonmatching_backlog_is_claimable(platform):
    p=platform
    for i in range(205):queue(p,labels=['other-runner'])
    wanted=queue(p,labels=['this-runner'])
    r=register(p,['this-runner'])
    assert claim(p,r)['id']==wanted['id']
