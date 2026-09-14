from __future__ import annotations
import asyncio
from copy import deepcopy
import json
import math
from pathlib import Path
import httpx
import pytest
from ordeal_agent import CallableAgent, ToolOrder, ToolCalled
from ordeal_agent.models import Usage, Verdict
from ordeal_agent.runner import Runner
from ordeal_agent.telemetry import PlatformClient
from ordeal_agent.provider_limits import ProviderLimiter, RetryPolicy
from ordeal_platform.ci import gate_report
from ordeal_platform import services
from conftest import sample_trace

@pytest.mark.parametrize('payload',[[],None,1,{'resourceSpans':None},{'resourceSpans':[None]}, {'resourceSpans':[{'scopeSpans':True}]}])
def test_malformed_otlp_is_not_server_error(platform,payload):
    p=platform
    response=p.client.post('/v1/traces',headers={**p.headers,'X-Ordeal-Project':p.project,'Content-Type':'application/json'},content=json.dumps(payload))
    assert response.status_code in {400,422},response.text

@pytest.mark.parametrize('payload',[[],None,1,{'members':True,'displayName':'bad'},{'members':[{}],'displayName':'bad'}])
def test_malformed_scim_is_not_server_error(platform,payload):
    p=platform
    response=p.client.post('/scim/v2/Groups',headers=p.headers,json=payload)
    assert response.status_code in {400,422},response.text

@pytest.mark.parametrize('value',[True,-1,float('inf'),float('nan'),.5])
def test_invalid_usage_tokens_rejected(value):
    with pytest.raises(ValueError):Usage.from_mapping({'total_tokens':value})

def test_unknown_usage_is_not_free():
    assert Usage().to_dict()['cost_usd'] is None
    assert Usage.from_mapping({'cost_usd':0}).to_dict()['cost_usd'] == 0

@pytest.mark.asyncio
async def test_provider_concurrency_cancellation_and_explicit_retries():
    active=peak=calls=0
    async def transport(request):
        nonlocal active,peak,calls
        calls+=1;active+=1;peak=max(peak,active)
        await asyncio.sleep(.01);active-=1
        return httpx.Response(200,json={'ok':True})
    limiter=ProviderLimiter(concurrency=2,requests_per_second=10000)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        await asyncio.gather(*(limiter.request(client,'POST','https://provider.test') for _ in range(12)))
        assert peak == 2 and calls == 12 and limiter.active == 0
    attempt=0
    async def failing(request):
        nonlocal attempt;attempt+=1
        return httpx.Response(429 if attempt < 3 else 200,headers={'retry-after':'0'},json={})
    limiter=ProviderLimiter(concurrency=4,requests_per_second=10000)
    async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as client:
        response=await limiter.request(client,'POST','https://provider.test',retry=RetryPolicy(base_delay=0,max_delay=.01))
        assert response.status_code==429 and attempt==1
        response=await limiter.request(client,'POST','https://provider.test',idempotent=True,retry=RetryPolicy(base_delay=0,max_delay=.01))
        assert response.status_code==200 and attempt==3 and limiter.limit==1
    limiter=ProviderLimiter(concurrency=1,requests_per_second=10000)
    async with limiter.slot():
        task=asyncio.create_task(limiter.slot().__aenter__());await asyncio.sleep(.01);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    assert limiter.active==0


def test_python_client_rejects_foreign_origins():
    with PlatformClient('http://localhost:8080','token','project') as client:
        for path in ['https://evil.test','//evil.test','/\\evil.test']:
            with pytest.raises(ValueError):client.request('GET',path)


def test_full_production_to_dataset_to_replay_regression(live_server):
    p=live_server.platform
    with PlatformClient(live_server.url,p.owner['token'],p.project) as client:
        with client.trace('payments-production',fail_open=False) as trace:
            with trace.span('authorize',arguments={'id':'A100'}) as span:span.set_output({'allow':True})
            with trace.span('transfer',arguments={'id':'A100'}) as span:span.set_output({'ok':True})
            trace.status='pass'
        dataset=client.create('dataset','payment-regression',{'cases':[]})
        client.request('POST',f'/api/v1/traces/{trace.response["id"]}/dataset',{'dataset_id':dataset['id'],'base_version':1,'instruction':'Transfer after approval','expected':'done'})
        suite=client.replay_dataset(dataset['id'],version=2,assertions=[ToolOrder('authorize','transfer'),ToolCalled('transfer')])
        async def good(request):
            approval=await request.runtime.call('authorize',id='A100')
            if approval['allow']:await request.runtime.call('transfer',id='A100')
            return 'done'
        async def broken(request):
            await request.runtime.call('transfer',id='A100')
            return 'done'
        before=asyncio.run(Runner().run_suite(CallableAgent(good,name='payments',version='1'),suite))
        after=asyncio.run(Runner().run_suite(CallableAgent(broken,name='payments',version='2'),suite))
        assert before.pass_rate==1 and after.pass_rate==0
        assert gate_report(before.to_dict())['passed']
        assert not gate_report(after.to_dict(),baseline=before.to_dict())['passed']
        client.upload_report(before,name='Payments v1');client.upload_report(after,name='Payments v2')
        assert len(client.request('GET',f'/api/v1/projects/{p.project}/resources?kind=experiment')['items'])==2


def test_trace_cassette_preserves_call_order_not_completion_order():
    data=sample_trace()
    events=data['trajectory']['events']
    data['trajectory']['events']=[events[0],events[2],events[3],events[1]]
    case=services.trace_case({'id':'t','name':'parallel','content_hash':'hash','data':data},'do it')
    assert [c['tool'] for c in case['cassette']['calls']]==['authorize','refund']
    data['trajectory']['events'][0]['payload']['arguments']={'email':'[EMAIL]'}
    assert not services.trace_case({'id':'t','name':'redacted','content_hash':'hash','data':data},'do it')['replay_ready']

@pytest.mark.asyncio
async def test_scenario_without_assertions_is_incomplete_not_pass():
    from ordeal_agent import Scenario,World
    result=await Runner().run_scenario(CallableAgent(lambda request:'okay'),Scenario('unscored','test',World('w')))
    assert result.verdict==Verdict.INCOMPLETE


def test_pinned_prompt_rendering_rejects_expressions(platform):
    p=platform
    resource=p.resource('prompt','instructions',{'template':'Hello {role}. {{literal}}','variables':['role']})
    result=p.call('POST',f'/api/v1/resources/{resource["id"]}/render',json={'version':1,'variables':{'role':'reviewer'}})
    assert result['text']=='Hello reviewer. {literal}' and result['version']==1
    p.call('POST',f'/api/v1/resources/{resource["id"]}/render',expected=422,json={'variables':{'role':'reviewer'}})
    p.call('POST',f'/api/v1/resources/{resource["id"]}/render',expected=422,json={'version':1,'variables':{'role':'reviewer','extra':'x'}})
    for template in ['{user.__class__}','{user[key]}','{role!r}','{role:>1000000000}']:
        p.call('POST',f'/api/v1/projects/{p.project}/resources',expected=422,json={'kind':'prompt','name':'bad','data':{'template':template}})


def test_enterprise_workflow_examples():
    from ordeal_agent.discovery import load_target
    target=Path(__file__).resolve().parents[2]/'examples/enterprise/suite.py'
    agent,suite=load_target(str(target))
    report=Runner().run_suite_sync(agent,suite)
    assert report.pass_rate==1 and len(report.results)==9


def test_mounted_secrets_are_explicit_and_fail_closed(tmp_path,monkeypatch):
    from ordeal_platform.config import env_value,Settings
    secret=tmp_path/'secret';secret.write_text('mounted-value\n')
    monkeypatch.setenv('ORDEAL_API_KEY_FILE',str(secret))
    assert env_value('ORDEAL_API_KEY')=='mounted-value'
    monkeypatch.setenv('ORDEAL_API_KEY','other')
    with pytest.raises(ValueError,match='not both'):env_value('ORDEAL_API_KEY')
    monkeypatch.delenv('ORDEAL_API_KEY')
    secret.unlink()
    with pytest.raises(ValueError,match='regular file'):env_value('ORDEAL_API_KEY')


@pytest.mark.parametrize('sample',[True,'1',None,[],{}])
def test_invalid_online_sampling_input_is_client_error(platform,sample):
    p=platform
    p.call('PUT',f'/api/v1/projects/{p.project}/settings',expected=422,
           json={'online_evaluators':[{'id':'unknown','version':1,'sample_rate':sample}]})


@pytest.mark.asyncio
async def test_injected_outputs_cannot_mutate_recorded_evidence():
    from ordeal_agent import Fault,World,simulated
    world=World('test',tools=[simulated('tool',lambda args,ctx:{'x':1})])
    run=world.spawn('case',faults=[Fault.returning('tool',{'items':[1]})])
    output=await run.call_tool('tool',{})
    output['items'].append(2)
    result=[e for e in run.trajectory.events if e.kind=='tool_result'][0]
    assert result.payload['result']=={'items':[1]}


def test_production_telemetry_snapshots_mutable_arguments_and_results():
    from ordeal_agent.telemetry import TraceSession
    class Capture:
        def ingest(self, envelope):
            self.envelope = envelope
            return {"id": "captured"}
    client = Capture()
    arguments = {"order": {"id": "A100"}}
    result = {"allowed": [True]}
    with TraceSession(client, "snapshot", fail_open=False) as trace:
        with trace.span("authorize", arguments=arguments) as span:
            arguments["order"]["id"] = "A200"
            assert span.set_output(result) is result
            result["allowed"].append(False)
        output = {"done": [1]}
        trace.set_output(output)
        output["done"].append(2)
    events = client.envelope["trajectory"]["events"]
    assert events[0]["payload"]["arguments"]["order"]["id"] == "A100"
    assert events[1]["payload"]["result"] == {"allowed": [True]}
    assert client.envelope["trajectory"]["final_output"] == {"done": [1]}


def test_new_local_database_directory_is_private(tmp_path):
    import os
    import stat
    from ordeal_platform.db import Database
    if os.name != "posix":
        pytest.skip("POSIX permission semantics")
    directory = tmp_path / "private-store"
    database = Database(f"sqlite:///{directory}/platform.db")
    try:
        database.migrate()
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    finally:
        database.engine.dispose()
