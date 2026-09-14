from __future__ import annotations
import asyncio
from copy import deepcopy
import gzip
import json
import pytest
from ordeal_agent.recording import Cassette, RecordedCall
from ordeal_platform import behavior
from ordeal_platform.evaluators import evaluate
from conftest import sample_trace


def test_trace_idempotence_redaction_and_tamper_conflict(platform):
    p = platform
    payload = sample_trace(trace_id='unique-source-event', metadata={'email': 'person@example.com', 'api_key': 'verysecret',
        'encoded': '{"password":"hidden-value", "address":"person@example.com"}'})
    first = p.call('POST', f'/api/v1/projects/{p.project}/traces', json=payload)
    second = p.call('POST', f'/api/v1/projects/{p.project}/traces', json=payload)
    assert first['id'] == second['id']
    record = p.call('GET', f'/api/v1/traces/{first["id"]}')
    encoded = json.dumps(record)
    assert 'verysecret' not in encoded and 'hidden-value' not in encoded and 'person@example.com' not in encoded
    payload['name'] = 'different'
    p.call('POST', f'/api/v1/projects/{p.project}/traces', expected=409, json=payload)
    assert p.call('GET', f'/api/v1/projects/{p.project}/traces')['total'] == 1


def test_trace_to_dataset_then_isolated_repeated_simulation(platform):
    p = platform
    trace = p.trace()
    dataset = p.resource('dataset', 'regression', {'cases': []})
    converted = p.call('POST', f'/api/v1/traces/{trace["id"]}/dataset',
                       json={'dataset_id': dataset['id'], 'base_version': 1, 'instruction': 'Refund A100'})
    case = converted['case']
    assert case['replay_ready'] is True
    assert converted['dataset']['version'] == 2
    assert p.call('GET', f'/api/v1/resources/{dataset["id"]}?version=1')['data']['cases'] == []
    spec = case['cassette']
    cassette = Cassette(spec['name'], [RecordedCall(**x) for x in spec['calls']], spec['initial_state'])
    world = cassette.to_world()
    async def one(i):
        run = world.spawn('run-' + str(i))
        a = await run.call_tool('authorize', {'order': 'A100'})
        assert a == {'allowed': True}
        a['allowed'] = False
        b = await run.call_tool('refund', {'order': 'A100'})
        assert b == {'ok': True}
        assert run.trajectory.events[1].payload['result']['allowed'] is True
    async def batch():
        await asyncio.gather(*(one(i) for i in range(20)))
    asyncio.run(batch())
    assert cassette.calls[0].result['allowed'] is True


def test_missing_result_never_becomes_replay_ready(platform):
    p = platform
    payload = sample_trace()
    payload['trajectory']['events'] = payload['trajectory']['events'][:1]
    trace = p.call('POST', f'/api/v1/projects/{p.project}/traces', json=payload)
    data = p.resource('dataset', 'missing', {'cases': []})
    result = p.call('POST', f'/api/v1/traces/{trace["id"]}/dataset', json={'dataset_id': data['id'], 'base_version': 1})
    assert result['case']['replay_ready'] is False
    assert result['case']['capture_gaps']


@pytest.mark.parametrize('spec,expected', [
    ({'type': 'tool_called', 'tool': 'authorize'}, 'pass'),
    ({'type': 'tool_called', 'tool': 'absent'}, 'fail'),
    ({'type': 'tool_not_called', 'tool': 'delete'}, 'pass'),
    ({'type': 'tool_not_called', 'tool': 'refund'}, 'fail'),
    ({'type': 'tool_order', 'before': 'authorize', 'after': 'refund'}, 'pass'),
    ({'type': 'tool_order', 'before': 'refund', 'after': 'authorize'}, 'fail'),
    ({'type': 'tool_order', 'before': 'missing', 'after': 'refund'}, 'incomplete'),
    ({'type': 'state_equals', 'key': 'refunded', 'value': True}, 'pass'),
    ({'type': 'state_equals', 'key': 'absent', 'value': True}, 'incomplete'),
    ({'type': 'output_contains', 'text': 'Refund'}, 'pass'),
    ({'type': 'json_schema', 'schema': {'type': 'string'}}, 'pass'),
    ({'type': 'max_cost', 'maximum': .01}, 'pass'),
    ({'type': 'max_tokens', 'maximum': 50}, 'fail'),
    ({'type': 'max_latency', 'maximum': 40}, 'fail'),
    ({'type': 'human'}, 'incomplete'),
    ({'type': 'python', 'entrypoint': 'eval.py:check'}, 'incomplete'),
])
def test_evaluator_semantics(spec, expected):
    assert evaluate(spec, sample_trace())['verdict'] == expected


def test_unknown_measurement_and_snapshot_fail_closed():
    trace = sample_trace(usage={}, metadata={'completeness': 'unknown'})
    assert evaluate({'type': 'max_cost', 'maximum': 5}, trace)['verdict'] == 'incomplete'
    assert evaluate({'type': 'tool_not_called', 'tool': 'dangerous'}, trace)['verdict'] == 'incomplete'
    assert evaluate({'type': 'tool_not_called', 'tool': 'refund'}, trace)['verdict'] == 'fail'


def test_pinned_evaluation_persisted_and_review(platform):
    p = platform
    t = p.trace()
    e = p.resource('evaluator', 'ordering', {'type': 'tool_order', 'before': 'authorize', 'after': 'refund'})
    result = p.call('POST', f'/api/v1/traces/{t["id"]}/evaluate', json={'evaluator_id': e['id'], 'version': 1})
    assert result['result']['verdict'] == 'pass'
    p.call('POST', f'/api/v1/resources/{e["id"]}/versions', expected=201,
           json={'base_version': 1, 'data': {'type': 'tool_order', 'before': 'refund', 'after': 'authorize'}})
    old = p.call('GET', f'/api/v1/traces/{t["id"]}')['evaluations'][0]
    assert old['evaluator_version'] == 1 and old['result']['verdict'] == 'pass'
    review = p.call('POST', f'/api/v1/projects/{p.project}/reviews', expected=201, json={'trace_id': t['id']})
    p.call('PATCH', f'/api/v1/reviews/{review["id"]}', json={'status': 'approved', 'labels': {'correct': True}, 'comment': 'Reviewed'})
    assert p.call('GET', f'/api/v1/projects/{p.project}/reviews')['items'][0]['status'] == 'approved'


def test_behavior_calibration_and_diff():
    stats = behavior.calibration([{'human': True, 'judge': True}, {'human': False, 'judge': False},
                                  {'human': True, 'judge': False}, {'human': False, 'judge': True}])
    assert stats['accuracy'] == .5
    assert stats['precision'] == .5 and stats['recall'] == .5
    assert stats['cohen_kappa'] == 0
    a = sample_trace()
    b = sample_trace()
    b['trajectory']['events'] = b['trajectory']['events'][2:]
    value = behavior.drift([a], [b])
    assert value['jensen_shannon_divergence'] == 1


def test_privacy_suppression_and_quota(platform):
    p = platform
    p.call('PUT', f'/api/v1/projects/{p.project}/settings', json={'suppress_inputs': True, 'suppress_outputs': True,
        'limits': {'traces_daily': 1, 'bytes_daily': 100000}})
    t = p.trace()
    full = p.call('GET', f'/api/v1/traces/{t["id"]}')
    assert 'Refund completed' not in json.dumps(full)
    assert 'A100' not in json.dumps(full)
    from conftest import sample_trace
    p.call('POST', f'/api/v1/projects/{p.project}/traces', expected=429, json=sample_trace(name='second'))


@pytest.mark.parametrize('body', [b'{', b'[]', b'null', b'"text"', b'{"usage":{"cost_usd":-1}}'])
def test_malformed_trace_rejected(platform, body):
    p = platform
    r = p.client.post(f'/api/v1/projects/{p.project}/traces', headers={**p.headers, 'Content-Type': 'application/json'}, content=body)
    assert r.status_code == 422


def test_gzip_valid_and_bomb_size_limit(platform):
    p = platform
    path = f'/api/v1/projects/{p.project}/traces'
    headers = {**p.headers, 'Content-Type': 'application/json', 'Content-Encoding': 'gzip'}
    r = p.client.post(path, headers=headers, content=gzip.compress(json.dumps(sample_trace()).encode()))
    assert r.status_code == 200, r.text
    r = p.client.post(path, headers=headers, content=gzip.compress(b' ' * (p.settings.max_body_bytes + 1)))
    assert r.status_code == 413
    r = p.client.post(path, headers=headers, content=b'not gzip')
    assert r.status_code == 400
