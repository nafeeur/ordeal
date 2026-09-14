"""Test an already installed wheel, through actual CLI and HTTP processes.

Run with the target environment's Python. Uses only temporary data and fake
agent fixtures; no credentials leave the loopback server. Third-party dependency
installation must be arranged independently before invoking this script.
"""
from __future__ import annotations
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import httpx
from ordeal_agent import CallableAgent, ToolCalled, ToolOrder
from ordeal_agent.runner import Runner
from ordeal_agent.telemetry import PlatformClient
from ordeal_platform.ci import gate_report
import ordeal_agent
import ordeal_platform

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT/'release-evidence/installed-smoke.json')
    args = parser.parse_args()
    assert ROOT/'src' not in Path(ordeal_agent.__file__).parents, 'Must run against an installed wheel, not src'
    result = {'version': importlib.metadata.version('ordeal-agent'), 'python':sys.version.split()[0], 'phases': []}
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='ordeal-wheel-smoke-') as temp:
        temp = Path(temp)
        env = {k:v for k,v in os.environ.items() if not k.startswith('ORDEAL_') and k not in {'PYTHONPATH','PYTHONHOME'}}
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        env.update(ORDEAL_DATA_DIR=str(temp/'data'), ORDEAL_PUBLIC_URL=url, ORDEAL_REQUEST_LIMIT='100000')
        server = None
        log = open(temp/'server.log','w')
        def cmd(*parts, code=0, run_env=None, cwd=temp):
            proc = subprocess.run([sys.executable,'-m',*parts], cwd=cwd, env=run_env or env,
                                  capture_output=True, text=True, timeout=40)
            assert proc.returncode == code, f'Command {parts[0:2]} returned {proc.returncode}: {proc.stderr[-1500:]}'
            return proc.stdout
        def start():
            nonlocal server
            server = subprocess.Popen([sys.executable,'-m','ordeal_platform.cli','serve','--port',str(port)],
                                      cwd=temp, env=env, stdout=log, stderr=log)
            deadline = time.monotonic()+15
            with httpx.Client(trust_env=False) as probe:
                while time.monotonic()<deadline:
                    assert server.poll() is None, 'API process exited before readiness'
                    try:
                        if probe.get(url+'/readyz',timeout=.25).status_code == 200: return
                    except httpx.HTTPError: pass
                    time.sleep(.05)
            raise AssertionError('API failed readiness')
        def stop():
            nonlocal server
            if server is not None:
                server.terminate()
                try: server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill();server.wait();raise AssertionError('API did not stop cleanly')
                server=None
        try:
            created=json.loads(cmd('ordeal_platform.cli','init','--name','Wheel smoke'))
            token,project=created['token'],created['project_id']
            assert json.loads(cmd('ordeal_platform.cli','init'))['organizations']==1
            result['phases'].append('installed CLI init and idempotent migration')
            start()
            with PlatformClient(url,token,project) as client:
                assert client.request('GET','/api/v1/me')['tenant_id']==created['tenant_id']
                with httpx.Client(trust_env=False) as probe:
                    html=probe.get(url).text
                    assert 'Ordeal' in html
                    assert probe.get(url+'/static/app.js').status_code==200
                    assert probe.get(url+'/trust').status_code==200
                result['phases'].append('packaged API, static console and trust page')
                with client.trace('wheel-production',fail_open=False) as trace:
                    with trace.span('authorize',arguments={'id':'A100'}) as span: span.set_output({'allowed':True})
                    with trace.span('transfer',arguments={'id':'A100'}) as span: span.set_output({'ok':True})
                    trace.status='pass'
                trace_id=trace.response['id']
                dataset=client.create('dataset','wheel-regression',{'cases':[]})
                client.request('POST',f'/api/v1/traces/{trace_id}/dataset',{'dataset_id':dataset['id'],'base_version':1,'instruction':'Authorize then transfer'})
                suite=client.replay_dataset(dataset['id'],version=2,assertions=[ToolCalled('transfer'),ToolOrder('authorize','transfer')])
                async def good(request):
                    await request.runtime.call('authorize',id='A100');await request.runtime.call('transfer',id='A100');return 'done'
                async def bad(request):
                    await request.runtime.call('transfer',id='A100');return 'done'
                before=asyncio.run(Runner().run_suite(CallableAgent(good),suite))
                after=asyncio.run(Runner().run_suite(CallableAgent(bad),suite))
                assert before.pass_rate==1 and after.pass_rate==0
                assert gate_report(before.to_dict())['passed']
                assert not gate_report(after.to_dict(),baseline=before.to_dict())['passed']
                baseline=temp/'baseline.json';candidate=temp/'candidate.json'
                baseline.write_text(json.dumps(before.to_dict()));candidate.write_text(json.dumps(after.to_dict()))
                cmd('ordeal_platform.cli','gate',str(baseline));cmd('ordeal_platform.cli','gate',str(candidate),'--baseline',str(baseline),code=1)
                client.upload_report(before,name='Working agent');client.upload_report(after,name='Broken agent')
                result['phases'].append('production trace -> versioned dataset -> replay -> failing regression CLI exit')
                job=client.enqueue('examples/enterprise/suite.py',idempotency_key='wheel-runner')
                runner_env={**env,'ORDEAL_API_KEY':token}
                cmd('ordeal_platform.cli','runner','--server',url,'--project',project,'--workspace',str(ROOT),
                    '--allow-suite','examples/enterprise/suite.py','--mode','trusted-process','--once',run_env=runner_env)
                complete=client.request('GET',f'/api/v1/jobs/{job["id"]}')
                assert complete['state']=='succeeded' and len(complete['result']['report']['results'])==9
                result['phases'].append('independent runner CLI -> real agent child process -> nine passing cases')
                payload=b'encrypted wheel smoke artifact'
                stored=client.http.post(f'/api/v1/projects/{project}/artifacts',content=payload);stored.raise_for_status()
                artifact_id=stored.json()['id']
                # Bounded local concurrency smoke. This is not a capacity or SLO benchmark.
                count=100
                def ingest(i):
                    envelope={'trace_id':f'wheel-load-{i:08d}','name':'concurrency-smoke','status':'unset',
                              'trajectory':{'events':[]},'metadata':{'load_smoke':True}}
                    start_at=time.perf_counter(); out=client.ingest(envelope)
                    return out['id'],(time.perf_counter()-start_at)*1000
                start_at=time.perf_counter()
                with ThreadPoolExecutor(max_workers=8) as pool: observations=list(pool.map(ingest,range(count)))
                elapsed=time.perf_counter()-start_at
                assert len({ident for ident,_ in observations})==count
                ids=[ident for ident,_ in observations]
                for i in (0,49,99):
                    assert client.request('GET',f'/api/v1/traces/{ids[i]}')['name']=='concurrency-smoke'
                timings=sorted(t for _,t in observations)
                result['bounded_load_smoke']={'requests':count,'concurrency':8,'errors':0,'seconds':round(elapsed,3),
                    'requests_per_second':round(count/elapsed,2),'median_ms':round(statistics.median(timings),2),
                    'p95_ms':round(timings[int(.95*(len(timings)-1))],2),
                    'qualification':'Loopback HTTP, SQLite, synthetic minimal traces. Not a scale, latency-SLO or sustained-load claim.'}
                result['phases'].append('100 unique ingestions with eight concurrent clients')
            stop();start()
            with PlatformClient(url,token,project) as client:
                assert client.request('GET',f'/api/v1/traces/{trace_id}')['name']=='wheel-production'
                assert client.request('GET',f'/api/v1/jobs/{job["id"]}')['state']=='succeeded'
                result['phases'].append('API process restart preserves traces and jobs')
            stop()
            archive=temp/'backup.ordeal'
            backup=json.loads(cmd('ordeal_platform.cli','backup',str(archive)))
            assert backup['encrypted'] is True and payload not in archive.read_bytes()
            restored=temp/'restored'
            cmd('ordeal_platform.cli','restore',str(archive),str(restored))
            env['ORDEAL_MASTER_KEY_FILE']=str(temp/'data/master.key')
            env['ORDEAL_DATA_DIR']=str(restored)
            start()
            with PlatformClient(url,token,project) as client:
                assert client.request('GET',f'/api/v1/traces/{trace_id}')['name']=='wheel-production'
                downloaded=client.http.get(f'/api/v1/artifacts/{artifact_id}');downloaded.raise_for_status()
                assert downloaded.content==payload
                assert client.request('GET','/api/v1/audit/checkpoint')['checkpoint']['valid'] is True
            result['phases'].append('encrypted backup CLI -> empty-directory restore -> restarted API and artifact recovery')
            cmd('ordeal_agent.cli','init')
            cmd('ordeal_agent.cli','run','ordeal_tests/test_agent.py','--min-pass-rate','1.0')
            result['phases'].append('installed local SDK scaffold runs without server dependency')
        finally:
            stop();log.close()
    result['elapsed_seconds']=round(time.monotonic()-started,3)
    result['passed']=True
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
