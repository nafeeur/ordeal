from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from ordeal_agent.telemetry import PlatformClient
from ordeal_platform.runner import CustomerRunner, RunnerPolicy
from ordeal_platform.provisioning import apply_manifest, install_pack

ROOT = Path(__file__).resolve().parents[2]


def test_production_sdk_to_real_server_and_customer_runner(live_server):
    p = live_server.platform
    with PlatformClient(live_server.url, p.owner['token'], p.project) as client:
        with client.trace('real-python-client', fail_open=False) as trace:
            with trace.span('authorize', arguments={'order':'A100'}) as span:
                span.set_output({'allowed':True})
            with trace.span('refund', arguments={'order':'A100'}) as span:
                span.set_output({'ok':True})
            trace.status = 'pass'
            trace.set_output('Refund completed')
        report = client.request('GET', f'/api/v1/traces/{trace.response["id"]}')
        assert report['name'] == 'real-python-client'
        assert len(report['data']['trajectory']['events']) == 4
        assert report['cost_usd'] is None
        queued = client.enqueue('examples/customer_support/suite.py', idempotency_key='runner-full-loop')
        policy = RunnerPolicy(ROOT, ['examples/customer_support/suite.py'], mode='trusted-process')
        runner = CustomerRunner(client, policy)
        completed = runner.run_once()
        assert completed['job_id'] == queued['id'] and completed['state'] == 'succeeded'
        assert runner.run_once() is None
        job = client.request('GET', f'/api/v1/jobs/{queued["id"]}')
        assert len(job['result']['report']['results']) == 2
        assert len(client.request('GET', f'/api/v1/projects/{p.project}/traces')['items']) == 3


def test_custom_python_evaluator_runs_only_on_opted_in_runner(live_server, tmp_path):
    p = live_server.platform
    (tmp_path / 'quality.py').write_text('def check(trace, options):\n    return {"verdict": "pass", "score": 1.0}\n')
    trace = p.trace()
    evaluator = p.resource('evaluator', 'custom', {'type':'python', 'entrypoint':'quality.py:check'})
    with PlatformClient(live_server.url, p.owner['token'], p.project) as client:
        queued = client.request('POST', f'/api/v1/projects/{p.project}/jobs', {'kind':'evaluate', 'payload':{
            'trace_id': trace['id'], 'evaluator_id':evaluator['id'], 'version':1}})
        assert 'custom-evaluator' in queued['labels']
        policy = RunnerPolicy(tmp_path, ['quality.py'], mode='trusted-process')
        standard = CustomerRunner(client, policy)
        assert standard.run_once() is None
        opted_in = CustomerRunner(client, policy, custom_evaluators=True)
        assert opted_in.run_once()['state'] == 'succeeded'
        evaluations = client.request('GET', f'/api/v1/traces/{trace["id"]}')['evaluations']
        assert evaluations[0]['result']['source'] == 'customer-runner'


def test_manifest_and_checksum_pack_against_real_api(live_server, tmp_path):
    import hashlib
    p = live_server.platform
    with PlatformClient(live_server.url, p.owner['token'], p.project) as client:
        manifest = {'schema':'ordeal.manifest/v1','resources':[{'kind':'dataset','name':'provisioned','data':{'cases':[]}}]}
        assert apply_manifest(client, manifest)['changes'][0]['action'] == 'created'
        assert apply_manifest(client, manifest)['changes'][0]['action'] == 'unchanged'
        manifest['resources'][0]['data']['cases'].append({'id':'new-case'})
        assert apply_manifest(client, manifest)['changes'][0]['action'] == 'versioned'
        path = tmp_path / 'safe-pack.json'
        pack = {'schema':'ordeal.pack/v1','name':'baseline','resources':[{'kind':'evaluator','name':'never-delete','data':{'type':'tool_not_called','tool':'delete'}}]}
        raw = json.dumps(pack).encode(); path.write_bytes(raw)
        expected = hashlib.sha256(raw).hexdigest()
        assert install_pack(client,path,expected)['pack'] == 'baseline'
        with pytest.raises(ValueError): install_pack(client,path,'0'*64)
        pack['resources'][0]['data'] = {'type':'python','entrypoint':'untrusted.py:run'}
        raw = json.dumps(pack).encode(); path.write_bytes(raw)
        with pytest.raises(ValueError): install_pack(client,path,hashlib.sha256(raw).hexdigest())


def test_typescript_client_to_real_python_server(live_server):
    p = live_server.platform
    module = (ROOT / 'sdks/typescript/dist/index.js').as_uri()
    program = f'''
import assert from 'node:assert/strict';
import {{OrdealClient}} from {json.dumps(module)};
const client = new OrdealClient({{baseUrl:process.env.ORDEAL_TEST_URL,token:process.env.ORDEAL_TEST_TOKEN,projectId:process.env.ORDEAL_TEST_PROJECT}});
const dataset = await client.create('dataset','node-dataset',{{cases:[]}});
const next = await client.update(dataset.id,1,{{cases:[{{id:'typescript-case'}}]}});
assert.equal(next.version,2);
const old = await client.getResource(dataset.id,1);
assert.equal(old.data.cases.length,0);
let seen = 0;
for await (const item of client.resources('dataset')) {{assert.equal(item.kind,'dataset'); seen++}}
assert.equal(seen,1);
const trace = client.trace('real-node-agent',{{failOpen:false}});
await trace.step('authorize',{{order:'A100'}},()=>({{allowed:true}}));
await trace.step('refund',{{order:'A100'}},()=>({{ok:true}}));
trace.output = 'Refund completed'; trace.status = 'pass';
const saved = await trace.finish();
const fetched = await client.getTrace(saved.id);
assert.equal(fetched.data.trajectory.events.length,4);
const evaluator = await client.create('evaluator','node-order',{{type:'tool_order',before:'authorize',after:'refund'}});
const result = await client.evaluate(saved.id,evaluator.id,1);
assert.equal(result.result.verdict,'pass');
console.log(JSON.stringify({{datasetVersion:next.version,traceId:saved.id,evaluation:result.result.verdict}}));
'''
    env = {**os.environ, 'ORDEAL_TEST_URL':live_server.url, 'ORDEAL_TEST_TOKEN':p.owner['token'], 'ORDEAL_TEST_PROJECT':p.project}
    result = subprocess.run(['node','--input-type=module','-e',program],env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['evaluation'] == 'pass'
