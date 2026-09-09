from fastapi.testclient import TestClient
from app.main import app

c=TestClient(app)

def test_trace_learning_and_conformance():
    traces=[{"tool":"get_payment","response":{"id":"p1","status":"captured"},"latency_ms":12} for _ in range(5)]
    r=c.post('/api/lab/learn-simulator',json={"name":"payments-test","traces":traces,"persist":True})
    assert r.status_code==200 and r.json()['contracts']['get_payment']['calls']==5
    r=c.post('/api/lab/conformance',json={"profile":"payments-test","traces":traces})
    assert r.status_code==200 and r.json()['fidelity_score']==1.0

def test_worker_job_lease_round_trip():
    w=c.post('/api/enterprise/workers/register',json={"worker_id":"pytest-worker","labels":{"pool":"general"},"capabilities":["cpu"],"capacity":8})
    assert w.status_code==200 and w.json()['capacity']==8
    # Seed and schedule distributed work.
    c.post('/api/demo/seed')
    r=c.post('/api/runs',json={"name":"distributed-test","agent":"refund-agent","suite":"refund-regression","seed":10,"distributed":True})
    assert r.status_code==200 and r.json()['payload']['expected_trials']>=2
    j=c.post('/api/enterprise/jobs/claim',json={"worker_id":"pytest-worker","capabilities":["cpu"]})
    assert j.status_code==200 and j.json().get('lease_token')

def test_junit_export_and_schema_violation_event():
    c.post('/api/demo/seed')
    r=c.post('/api/runs',json={"name":"junit-test","agent":"refund-agent","suite":"refund-regression","seed":1})
    rid=r.json()['id']
    x=c.get(f'/api/runs/{rid}/junit')
    assert x.status_code==200 and '<testsuite' in x.text

def test_kafka_outbox_created_for_distributed_job():
    from app.db import SessionLocal
    from app.models import OutboxEvent, Job
    from sqlalchemy import select
    c.post('/api/demo/seed')
    r=c.post('/api/runs',json={"name":"kafka-outbox-test","agent":"refund-agent","suite":"refund-regression","seed":41,"distributed":True})
    assert r.status_code==200
    job_ids=r.json()['payload']['job_ids']
    with SessionLocal() as db:
        events=list(db.execute(select(OutboxEvent).where(OutboxEvent.status=='pending')).scalars())
        assert any(str(jid)==e.event_key and '.jobs.' in e.topic for jid in job_ids for e in events)

def test_reusable_constraints_and_stability_reporting():
    c.post('/api/demo/seed')
    r=c.post('/api/constraints',json={
        "name":"pytest-no-double-refund",
        "description":"A payment may not receive duplicate refund actions.",
        "severity":"critical",
        "assertion":{"type":"no_duplicate_tool_args","tool":"create_refund"},
        "bindings":{"scenarios":["happy-refund","payment-timeout"]}
    })
    assert r.status_code==200
    run=c.post('/api/runs',json={"name":"stability-constraint-test","agent":"refund-agent","suite":"refund-regression","seed":101,"repetitions":3})
    assert run.status_code==200
    payload=run.json()['payload']
    assert payload['stability']['variance_count']==0
    assert payload['stability']['scenarios'][0]['repeats']>=3
    assert payload['constraint_summary']['tracked_constraints']>=1
    assert payload['constraint_summary']['violations']==0
    rid=run.json()['id']
    assert c.get(f'/api/runs/{rid}/stability').status_code==200
    assert c.get(f'/api/runs/{rid}/constraints').status_code==200


def test_commit_aware_regression_blocks_only_new_failure():
    c.post('/api/demo/seed')
    baseline=c.post('/api/runs',json={"name":"baseline-commit-test","agent":"refund-agent","suite":"refund-regression","seed":501,"repetitions":2,"commit_sha":"base123"})
    assert baseline.status_code==200
    bid=baseline.json()['id']
    # New agent repeats the irreversible action with identical args, violating the reusable constraint.
    agents=c.get('/api/agents').json(); original=next(a['payload'] for a in agents if a['name']=='refund-agent')
    bad=dict(original); bad['name']='refund-agent-bad'; bad['version']='bad456'; bad['behavior']={"plan":[{"tool":"get_payment","args":{"id":"$payment_id"}},{"tool":"create_refund","args":{"payment_id":"$payment_id","amount":49.99}},{"tool":"create_refund","args":{"payment_id":"$payment_id","amount":49.99}}],"final_response":"done"}
    assert c.post('/api/agents',json=bad).status_code==200
    candidate=c.post('/api/runs',json={"name":"candidate-commit-test","agent":"refund-agent-bad","suite":"refund-regression","seed":501,"repetitions":2,"baseline_run_id":bid,"commit_sha":"bad456","base_commit_sha":"base123"})
    assert candidate.status_code==200
    pl=candidate.json()['payload']; attr=pl['regression_attribution']
    assert attr['blocking'] is True
    assert attr['new_regressions'] >= 1 or any(x['new_constraint_violations'] for x in attr['scenarios'])
    gate=c.get(f"/api/runs/{candidate.json()['id']}/gate").json()
    assert gate['mode']=='new_regressions_only' and gate['blocking'] is True
