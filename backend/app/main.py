import asyncio, copy, csv, io, json, random
from datetime import datetime
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from .db import Base, engine, SessionLocal
from .models import Agent, World, Scenario, Suite, Run, Dataset, SimulatorProfile, Constraint, Worker, Job, ApiKey, AuditLog, OutboxEvent
from .schemas import *
from .engine import SimulationEngine, evaluate_assertions, run_mock_agent, stable_hash
from .agent_runtime import run_openai_compatible
from .simulator_learning import learn_profile, conformance
from .causal import explain_failure
from .constraints import evaluate_constraints, stability_report, constraint_summary, regression_attribution
from .jobs import enqueue, register_worker, heartbeat, claim, complete, serialize_job, serialize_worker
from .security import Principal, principal_from_key, require_role, issue_api_key, audit
from .settings import settings
from .runtime_verifier import verify_runtime_execution

app=FastAPI(title="Ordeal Runtime Verification API", version="1.3.0", docs_url="/docs" if settings.environment!="production" else None)
origins=[x.strip() for x in settings.cors_origins.split(',') if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET","POST","PUT","DELETE"], allow_headers=["Content-Type","X-Ordeal-Key"])
Base.metadata.create_all(bind=engine)
sim=SimulationEngine()

def dumps(x): return json.dumps(x,separators=(",",":"),default=str)
def loads(x): return json.loads(x or "{}")
def record_to_dict(r): return {"id":r.id,"name":r.name,"payload":loads(r.payload),"created_at":r.created_at.isoformat(),"updated_at":getattr(r,"updated_at",r.created_at).isoformat()}

def upsert(model,name,payload,actor="system"):
    with SessionLocal() as db:
        r=db.execute(select(model).where(model.name==name)).scalar_one_or_none()
        if r:r.payload=dumps(payload)
        else:r=model(name=name,payload=dumps(payload));db.add(r)
        db.commit();db.refresh(r)
    audit(actor,"upsert",f"{model.__tablename__}/{name}")
    return record_to_dict(r)

@app.get("/health")
def health(): return {"ok":True,"service":"ordeal","version":"1.3.0","environment":settings.environment}

@app.get("/ready")
def ready():
    try:
        with SessionLocal() as db: db.execute(select(Agent.id).limit(1)).all()
        return {"ready":True,"database":True}
    except Exception as e: raise HTTPException(503,f"database unavailable: {e}")

@app.get("/api/summary")
def summary(p:Principal=Depends(principal_from_key)):
    with SessionLocal() as db:
        mapping={"agents":Agent,"worlds":World,"scenarios":Scenario,"suites":Suite,"runs":Run,"datasets":Dataset,"simulators":SimulatorProfile,"constraints":Constraint,"workers":Worker,"jobs":Job,"outbox":OutboxEvent}
        counts={k:db.query(m).count() for k,m in mapping.items()}
        recent=db.execute(select(Run).order_by(Run.id.desc()).limit(20)).scalars().all()
        online=sum(1 for w in db.execute(select(Worker)).scalars() if w.status=="online")
        return {**counts,"queue_backend":settings.queue_backend,"kafka_topic_prefix":settings.kafka_topic_prefix,"online_workers":online,"replays":sum(1 for r in recent if loads(r.payload).get("replay_of")),"recent_regressions":sum(int(loads(r.payload).get("regressions",0)) for r in recent)}

def _list_model(model):
    with SessionLocal() as db:return [record_to_dict(x) for x in db.execute(select(model).order_by(model.id.desc())).scalars()]
@app.get("/api/agents")
def list_agents(p:Principal=Depends(principal_from_key)):return _list_model(Agent)
@app.get("/api/worlds")
def list_worlds(p:Principal=Depends(principal_from_key)):return _list_model(World)
@app.get("/api/scenarios")
def list_scenarios(p:Principal=Depends(principal_from_key)):return _list_model(Scenario)
@app.get("/api/suites")
def list_suites(p:Principal=Depends(principal_from_key)):return _list_model(Suite)
@app.get("/api/datasets")
def list_datasets(p:Principal=Depends(principal_from_key)):return _list_model(Dataset)
@app.get("/api/simulators")
def list_simulators(p:Principal=Depends(principal_from_key)):return _list_model(SimulatorProfile)
@app.get("/api/constraints")
def list_constraints(p:Principal=Depends(principal_from_key)):return _list_model(Constraint)

@app.post("/api/agents")
def save_agent(spec:AgentSpec,p:Principal=Depends(require_role("operator"))):return upsert(Agent,spec.name,spec.model_dump(by_alias=True),p.name)
@app.post("/api/worlds")
def save_world(spec:WorldSpec,p:Principal=Depends(require_role("operator"))):return upsert(World,spec.name,spec.model_dump(by_alias=True),p.name)
@app.post("/api/scenarios")
def save_scenario(spec:ScenarioSpec,p:Principal=Depends(require_role("operator"))):return upsert(Scenario,spec.name,spec.model_dump(),p.name)
@app.post("/api/suites")
def save_suite(spec:SuiteSpec,p:Principal=Depends(require_role("operator"))):return upsert(Suite,spec.name,spec.model_dump(),p.name)
@app.post("/api/datasets")
def save_dataset(spec:DatasetSpec,p:Principal=Depends(require_role("operator"))):return upsert(Dataset,spec.name,spec.model_dump(),p.name)
@app.post("/api/constraints")
def save_constraint(spec:ConstraintSpec,p:Principal=Depends(require_role("operator"))):return upsert(Constraint,spec.name,spec.model_dump(),p.name)


@app.post("/api/runtime/verify")
def verify_runtime(req:RuntimeVerificationRequest,p:Principal=Depends(require_role("operator"))):
    """Verify an already-observed model trajectory without invoking a model."""
    result=verify_runtime_execution(req.model_dump())
    audit(p.name,"runtime.verify",f"executions/{req.execution}",{"verdict":result["verdict"],"fingerprint":result["fingerprint"]})
    return result

async def execute_trial(agent, scenario, world, seed):
    trial=sim.start_trial(world.get("state",{}),agent.get("tools",[]),scenario.get("faults",[]),seed,scenario)
    for setup in scenario.get("setup",[]): trial.ledger.commit(setup.get("op","set"),setup.get("path",""),setup.get("value"),source="scenario.setup")
    if agent.get("dispatch_type")=="http" and agent.get("endpoint"):
        import httpx
        async with httpx.AsyncClient(timeout=float(scenario.get("timeout_seconds",120))) as client:
            response=await client.post(agent["endpoint"],json={"trial_id":trial.id,"instruction":scenario.get("instruction",""),"variables":scenario.get("variables",{}),"tool_proxy_url":f"{settings.tool_proxy_base_url}/api/tool-proxy/{trial.id}","tools":[{"name":t.get("name"),"description":t.get("description",""),"input_schema":t.get("input_schema",{})} for t in agent.get("tools",[])]})
            response.raise_for_status(); body=response.json(); final=body.get("final_response",body.get("output","")) if isinstance(body,dict) else str(body)
    elif agent.get("dispatch_type")=="openai_compatible":
        final=await run_openai_compatible(agent,scenario,trial,sim,seed)
    else: final=await run_mock_agent(agent,scenario,trial,sim,seed)
    ledger=trial.ledger.export(); grades=evaluate_assertions(trial.state,trial.events,scenario.get("assertions",[]),ledger)
    with SessionLocal() as db:
        constraints=[loads(x.payload) for x in db.execute(select(Constraint)).scalars().all()]
    constraint_grades=evaluate_constraints(trial.state,trial.events,ledger,scenario,constraints,scenario.get("suite_name"))
    objective_grades=grades+constraint_grades
    passed=all(g["passed"] for g in objective_grades) if objective_grades else True
    return {"id":trial.id,"scenario":scenario.get("name"),"seed":seed,"passed":passed,"final_response":final,"initial_state":ledger["initial_state"],"final_state":trial.state,"initial_hash":ledger["initial_hash"],"final_hash":ledger["final_hash"],"events":trial.events,"ledger":ledger,"grades":grades,"constraint_grades":constraint_grades,"scenario_snapshot":copy.deepcopy(scenario),"world_snapshot":copy.deepcopy(world),"agent_snapshot":copy.deepcopy(agent),"execution_fingerprint":stable_hash({"agent":agent.get("name"),"version":agent.get("version"),"scenario":scenario,"world":world,"seed":seed})}

async def _resolve_run_inputs(run_id:int):
    with SessionLocal() as db:
        run=db.get(Run,run_id)
        if not run: raise HTTPException(404,"run not found")
        agent=loads(db.get(Agent,run.agent_id).payload); suite=loads(db.get(Suite,run.suite_id).payload)
        items=[]
        for scenario_name in suite.get("scenarios",[]):
            sr=db.execute(select(Scenario).where(Scenario.name==scenario_name)).scalar_one_or_none()
            if not sr: continue
            scenario=loads(sr.payload); scenario["name"]=scenario_name; scenario["suite_name"]=suite.get("name") or getattr(run,"name",None)
            wr=db.execute(select(World).where(World.name==scenario["world"])).scalar_one_or_none()
            if wr: items.append((scenario,loads(wr.payload)))
        return run,agent,suite,items

def _run_payload(trials,seed,extra=None):
    pc=sum(t["passed"] for t in trials)
    stability=stability_report(trials); constraints=constraint_summary(trials)
    return {"seed":seed,"trials":trials,"pass_count":pc,"fail_count":len(trials)-pc,"pass_rate":pc/len(trials) if trials else 0,"stability":stability,"constraint_summary":constraints,"run_fingerprint":stable_hash([{"scenario":t["scenario"],"seed":t["seed"],"final_hash":t["final_hash"],"passed":t["passed"]} for t in trials]),**(extra or {})}

@app.get("/api/runs")
def list_runs(p:Principal=Depends(principal_from_key)):
    with SessionLocal() as db:
        rows=db.execute(select(Run).order_by(Run.id.desc())).scalars()
        return [{"id":r.id,"name":r.name,"agent_id":r.agent_id,"suite_id":r.suite_id,"status":r.status,"payload":loads(r.payload),"created_at":r.created_at.isoformat()} for r in rows]

@app.post("/api/runs")
async def create_run(req:RunRequest,p:Principal=Depends(require_role("operator"))):
    with SessionLocal() as db:
        ar=db.execute(select(Agent).where(Agent.name==req.agent)).scalar_one_or_none(); sr=db.execute(select(Suite).where(Suite.name==req.suite)).scalar_one_or_none()
        if not ar or not sr: raise HTTPException(404,"agent or suite not found")
        run=Run(name=req.name,agent_id=ar.id,suite_id=sr.id,status="queued" if req.distributed else "running",payload="{}");db.add(run);db.commit();db.refresh(run);rid=run.id
    audit(p.name,"run.create",f"runs/{rid}",req.model_dump())
    meta={"commit_sha":req.commit_sha,"base_commit_sha":req.base_commit_sha,"baseline_run_id":req.baseline_run_id,"gate_on_new_regressions_only":req.gate_on_new_regressions_only}
    if req.distributed: return await schedule_distributed_run(rid,req.seed,max(1,req.repetitions),meta)
    result=await execute_run(rid,req.seed,max(1,req.repetitions),req.concurrency,meta)
    if req.baseline_run_id:
        result["payload"]["regression_attribution"]=_compare_payloads(req.baseline_run_id,rid)
        with SessionLocal() as db:
            r=db.get(Run,rid); r.payload=dumps(result["payload"]); db.commit()
    return result

async def execute_run(run_id:int,seed:int,repetitions:int=1,concurrency:int=16,meta:dict|None=None):
    run,agent,suite,items=await _resolve_run_inputs(run_id)
    sem=asyncio.Semaphore(max(1,min(concurrency,settings.max_parallel_trials)))
    async def one(sc,wo,s):
        async with sem:return await execute_trial(agent,sc,wo,s)
    tasks=[]; k=0
    for sc,wo in items:
        reps=max(repetitions,int(sc.get("repetitions",1)))
        for rep in range(reps): tasks.append(one(copy.deepcopy(sc),copy.deepcopy(wo),seed+k)); k+=1
    try:
        trials=await asyncio.gather(*tasks) if tasks else []
    except Exception as e:
        with SessionLocal() as db:
            r=db.get(Run,run_id); r.status="failed"; r.payload=dumps({"error":str(e)}); db.commit()
        raise
    payload=_run_payload(trials,seed,{"distributed":False,**(meta or {})})
    with SessionLocal() as db:
        r=db.get(Run,run_id); r.status="completed"; r.payload=dumps(payload); db.commit()
    return {"id":run_id,"name":run.name,"status":"completed","payload":payload}

async def schedule_distributed_run(run_id:int,seed:int,repetitions:int,meta:dict|None=None):
    run,agent,suite,items=await _resolve_run_inputs(run_id); job_ids=[]; k=0
    for sc,wo in items:
        reps=max(repetitions,int(sc.get("repetitions",1)))
        for rep in range(reps):
            capability="cpu"
            j=enqueue("trial.execute",{"run_id":run_id,"agent":agent,"scenario":sc,"world":wo,"seed":seed+k},capability); job_ids.append(j["id"]); k+=1
    with SessionLocal() as db:
        r=db.get(Run,run_id); r.status="queued"; r.payload=dumps({"seed":seed,"distributed":True,"job_ids":job_ids,"expected_trials":len(job_ids),"trials":[],**(meta or {})});db.commit()
    return {"id":run_id,"name":run.name,"status":"queued","payload":{"seed":seed,"distributed":True,"job_ids":job_ids,"expected_trials":len(job_ids),"trials":[],**(meta or {})}}

@app.post("/api/tool-proxy/{trial_id}/{tool_name}")
async def tool_proxy(trial_id:str,tool_name:str,body:dict):
    trial=sim.trials.get(trial_id)
    if not trial:raise HTTPException(404,"trial not found or expired")
    tool=trial.tools.get(tool_name)
    if not tool:raise HTTPException(404,"tool not registered for this trial")
    return await sim.tool_call(trial,tool,body,trial.faults,random.Random(trial.seed+sum(trial.call_counts.values())))

@app.get("/api/trials/{trial_id}")
def trial_state(trial_id:str,p:Principal=Depends(principal_from_key)):
    trial=sim.trials.get(trial_id)
    if not trial:raise HTTPException(404,"trial not found or expired")
    return {"id":trial.id,"state":trial.state,"events":trial.events,"call_counts":trial.call_counts,"ledger":trial.ledger.export()}

@app.post("/api/replay")
async def replay(req:ReplayRequest,p:Principal=Depends(require_role("operator"))):
    with SessionLocal() as db:
        source=db.get(Run,req.run_id)
        if not source:raise HTTPException(404,"run not found")
        original=next((t for t in loads(source.payload).get("trials",[]) if t.get("scenario")==req.scenario),None)
    if not original:raise HTTPException(404,"scenario not found in run")
    seed=req.seed if req.seed is not None else original.get("seed",1)
    result=await execute_trial(original["agent_snapshot"],original["scenario_snapshot"],original["world_snapshot"],seed)
    return {"source_run_id":req.run_id,"scenario":req.scenario,"original_final_hash":original.get("final_hash"),"replay_final_hash":result.get("final_hash"),"deterministic_match":original.get("final_hash")==result.get("final_hash") and original.get("passed")==result.get("passed"),"original_passed":original.get("passed"),"replay_passed":result.get("passed"),"trial":result}

def _compare_payloads(baseline_run_id:int,candidate_run_id:int):
    with SessionLocal() as db:
        a=db.get(Run,baseline_run_id); b=db.get(Run,candidate_run_id)
        if not a or not b: raise HTTPException(404,"run not found")
        pa,pb=loads(a.payload),loads(b.payload)
    attribution=regression_attribution(pa.get("trials",[]),pb.get("trials",[]))
    return {"baseline_run_id":a.id,"candidate_run_id":b.id,"baseline_pass_rate":pa.get("pass_rate",0),"candidate_pass_rate":pb.get("pass_rate",0),"delta":pb.get("pass_rate",0)-pa.get("pass_rate",0),**attribution}

@app.post("/api/compare")
def compare(req:CompareRequest,p:Principal=Depends(principal_from_key)):
    return _compare_payloads(req.baseline_run_id,req.candidate_run_id)

@app.get("/api/runs/{run_id}/stability")
def run_stability(run_id:int,p:Principal=Depends(principal_from_key)):
    with SessionLocal() as db:
        r=db.get(Run,run_id)
        if not r: raise HTTPException(404,"run not found")
        pl=loads(r.payload)
    return pl.get("stability") or stability_report(pl.get("trials",[]))

@app.get("/api/runs/{run_id}/constraints")
def run_constraints(run_id:int,p:Principal=Depends(principal_from_key)):
    with SessionLocal() as db:
        r=db.get(Run,run_id)
        if not r: raise HTTPException(404,"run not found")
        pl=loads(r.payload)
    return pl.get("constraint_summary") or constraint_summary(pl.get("trials",[]))

@app.get("/api/runs/{run_id}/gate")
def run_gate(run_id:int,p:Principal=Depends(principal_from_key)):
    with SessionLocal() as db:
        r=db.get(Run,run_id)
        if not r: raise HTTPException(404,"run not found")
        pl=loads(r.payload)
    attr=pl.get("regression_attribution")
    if attr is not None:
        return {"run_id":run_id,"mode":"new_regressions_only","blocking":bool(attr.get("blocking")),"new_regressions":attr.get("new_regressions",0),"details":attr}
    stable_fail=(pl.get("stability") or {}).get("stable_fail_count",0)
    violations=(pl.get("constraint_summary") or {}).get("violations",0)
    return {"run_id":run_id,"mode":"absolute","blocking":bool(stable_fail or violations),"stable_failures":stable_fail,"constraint_violations":violations}

@app.get("/api/runs/{run_id}/junit")
def junit(run_id:int,p:Principal=Depends(principal_from_key)):
    import xml.etree.ElementTree as ET
    with SessionLocal() as db:
        r=db.get(Run,run_id)
        if not r: raise HTTPException(404,"run not found")
        pl=loads(r.payload)
    suite=ET.Element("testsuite",name=r.name,tests=str(len(pl.get("trials",[]))),failures=str(pl.get("fail_count",0)))
    for t in pl.get("trials",[]):
        c=ET.SubElement(suite,"testcase",name=f"{t.get('scenario')}[{t.get('seed')}]")
        if not t.get("passed"):
            f=ET.SubElement(c,"failure",message="behavioral regression");f.text=json.dumps([g for g in t.get("grades",[]) if not g.get("passed")],default=str)
    return Response(ET.tostring(suite,encoding="unicode"),media_type="application/xml")

# World compilation / trace learning

def _plural(name:str):
    base=name.lower().replace('-','_'); return base if base.endswith('s') else base+'s'
def _tool_from_operation(path:str,method:str,op:dict):
    name=op.get('operationId') or f"{method}_{path.strip('/').replace('/','_').replace('{','').replace('}','')}"; method=method.lower(); key_arg='id'
    for x in op.get('parameters',[]):
        if x.get('in')=='path': key_arg=x.get('name','id');break
    noun=name
    for prefix in ('get_','list_','create_','update_','delete_','post_','put_','patch_'):
        if noun.startswith(prefix):noun=noun[len(prefix):]
    collection=_plural(noun.split('_by_')[0]); simop='lookup' if method=='get' and '{' in path else 'list' if method=='get' else 'create' if method=='post' else 'update' if method in {'put','patch'} else 'delete'
    response_schema={}
    try:
        response_schema=next(iter(op.get('responses',{}).values())).get('content',{}).get('application/json',{}).get('schema',{})
    except Exception: pass
    return {'name':name,'description':op.get('summary',''),'mode':'simulated','input_schema':{'type':'object'},'output_schema':response_schema,'simulation':{'op':simop,'collection':collection,'key_arg':key_arg}}

@app.post('/api/lab/compile-world')
def compile_world(req:CompileWorldRequest,p:Principal=Depends(require_role("operator"))):
    tools=[]; entities={};relationships=[];invariants=[]
    for path,methods in req.openapi.get('paths',{}).items():
        for method,op in methods.items():
            if method.lower() in {'get','post','put','patch','delete'} and isinstance(op,dict):
                t=_tool_from_operation(path,method,op);tools.append(t);entities.setdefault(t['simulation']['collection'],{})
    for mt in req.mcp_tools:
        name=mt.get("name");
        if name: tools.append({"name":name,"description":mt.get("description",""),"mode":"simulated","input_schema":mt.get("inputSchema",mt.get("input_schema",{})),"output_schema":mt.get("outputSchema",mt.get("output_schema",{})),"simulation":{"op":"template","response":{"ok":True}}})
    observed={}
    for tr in req.traces:
        tool=tr.get('tool') or tr.get('name');result=tr.get('response',tr.get('result'))
        if tool:
            observed.setdefault(tool,{'calls':0,'errors':0,'responses':[]});observed[tool]['calls']+=1
            if isinstance(result,dict) and result.get('error'):observed[tool]['errors']+=1
            if len(observed[tool]['responses'])<5:observed[tool]['responses'].append(result)
        after=tr.get('state_after')
        if isinstance(after,dict):
            for k,v in after.items():entities.setdefault(k,copy.deepcopy(v) if isinstance(v,(dict,list)) else {})
    names=' '.join(t['name'] for t in tools).lower()
    if 'refund' in names and 'payment' in names:
        invariants += [{'name':'refund_requires_payment','kind':'referential'},{'name':'no_duplicate_refund','kind':'uniqueness'}];relationships.append({'from':'refunds','to':'payments','via':'payment_id'})
    world={'name':req.name,'state':entities,'schema':{'entities':sorted(entities),'relationships':relationships,'invariants':invariants},'metadata':{'compiled':True,'sources':{'openapi_paths':len(req.openapi.get('paths',{})),'mcp_tools':len(req.mcp_tools),'traces':len(req.traces)},'observed_contracts':observed}}
    if req.persist:upsert(World,req.name,world,p.name)
    return {'world':world,'tools':tools,'coverage_hint':{'tool_contracts':len(tools),'observed_tools':len(observed)},'review_required':True}

@app.post('/api/lab/learn-simulator')
def learn_simulator(req:LearnSimulatorRequest,p:Principal=Depends(require_role("operator"))):
    profile=learn_profile(req.name,req.traces)
    if req.persist:upsert(SimulatorProfile,req.name,profile,p.name)
    return profile

@app.post('/api/lab/conformance')
def simulator_conformance(req:ConformanceRequest,p:Principal=Depends(principal_from_key)):
    with SessionLocal() as db:
        row=db.execute(select(SimulatorProfile).where(SimulatorProfile.name==req.profile)).scalar_one_or_none()
        if not row:raise HTTPException(404,"simulator profile not found")
        return conformance(loads(row.payload),req.traces)

async def _load_named_trial(agent_name,scenario_name):
    with SessionLocal() as db:
        ar=db.execute(select(Agent).where(Agent.name==agent_name)).scalar_one_or_none();sr=db.execute(select(Scenario).where(Scenario.name==scenario_name)).scalar_one_or_none()
        if not ar or not sr:raise HTTPException(404,'agent or scenario not found')
        agent=loads(ar.payload);scenario=loads(sr.payload);scenario['name']=scenario_name;wr=db.execute(select(World).where(World.name==scenario['world'])).scalar_one_or_none()
        if not wr:raise HTTPException(404,'world not found')
        return agent,scenario,loads(wr.payload)

def _coverage(agent,trials):
    declared={t.get('name') for t in agent.get('tools',[])};called=set();faulted=set();transitions=set();assertion_types=set();states=set()
    for t in trials:
        states.add(t.get("final_hash"))
        for e in t.get('events',[]):
            if e.get('tool'):called.add(e['tool'])
            if e.get('fault'):faulted.add(e.get('tool'))
            if e.get('state_before') and e.get('state_after') and e['state_before']!=e['state_after']:transitions.add(e.get('tool'))
        assertion_types|={g.get('assertion',{}).get('type') for g in t.get('grades',[])}
    pct=lambda n,d:round(100*n/d,1) if d else 100.0;missing=sorted(declared-called)
    return {'tool_coverage':pct(len(called),len(declared)),'mutation_coverage':pct(len(transitions),len(declared)),'fault_coverage':pct(len(faulted),len(declared)),'behavioral_states':len(states),'assertion_kinds':sorted(x for x in assertion_types if x),'uncovered_tools':missing,'suggestions':[f'Generate a scenario that calls {x}' for x in missing]}

@app.post('/api/lab/fuzz')
async def fuzz(req:FuzzRequest,p:Principal=Depends(require_role("operator"))):
    agent,base,world=await _load_named_trial(req.agent,req.scenario);rng=random.Random(req.seed);tools=req.fault_tools or [t['name'] for t in agent.get('tools',[])];trials=[];failure=None;seen=set()
    catalog=[{'error':'timeout'},{'error':'stale_read'},{'latency_ms':50,'response':{'error':'deadline'}},{'response':{'error':'permission_denied'}},{'response':{'error':'rate_limited'}},{'response':{'error':'connection_reset'}}]
    limit=max(1,min(req.iterations,settings.max_fuzz_iterations))
    for i in range(limit):
        s=copy.deepcopy(base);s['name']=f"{base['name']}::fuzz-{i:05d}";count=rng.randint(1,min(4,max(1,len(tools))));s['faults']=copy.deepcopy(base.get('faults',[]))
        for tool in rng.sample(tools,count):s['faults'].append({'tool':tool,'when':{'call':rng.choice([1,1,1,2])},'inject':copy.deepcopy(rng.choice(catalog))})
        t=await execute_trial(agent,s,world,req.seed+i);novel=t['final_hash'] not in seen;seen.add(t['final_hash']);t['behaviorally_novel']=novel;trials.append(t)
        if not t['passed']:failure=t;break
        if req.coverage_guided and not novel and len(trials)>20 and rng.random()<.35:continue
    return {'iterations_executed':len(trials),'failure_found':failure is not None,'failure':failure,'coverage':_coverage(agent,trials),'novel_states':len(seen),'search_fingerprint':stable_hash([{'s':t['scenario'],'h':t['final_hash'],'p':t['passed']} for t in trials])}

@app.post('/api/lab/shrink')
async def shrink_failure(req:ShrinkRequest,p:Principal=Depends(require_role("operator"))):
    with SessionLocal() as db:
        ar=db.execute(select(Agent).where(Agent.name==req.agent)).scalar_one_or_none()
        if not ar:raise HTTPException(404,'agent not found')
        agent=loads(ar.payload)
    scenario=copy.deepcopy(req.scenario);world=copy.deepcopy(req.world);original=await execute_trial(agent,scenario,world,req.seed)
    if original['passed']:return {'reducible':False,'reason':'scenario does not currently fail','scenario':scenario}
    removed=[]
    for key in ('faults','setup'):
        items=list(scenario.get(key,[]));i=0
        while i<len(items):
            candidate=items[:i]+items[i+1:];test=copy.deepcopy(scenario);test[key]=candidate;result=await execute_trial(agent,test,world,req.seed)
            if not result['passed']:removed.append({'kind':key,'item':items[i]});items=candidate;scenario[key]=candidate
            else:i+=1
    minimal=await execute_trial(agent,scenario,world,req.seed)
    return {'reducible':True,'removed':removed,'minimal_scenario':scenario,'minimal_failure':minimal,'explanation':f"Reduced failure to {len(scenario.get('faults',[]))} fault(s) and {len(scenario.get('setup',[]))} setup mutation(s)."}

@app.post('/api/lab/causal')
def causal(req:CausalRequest,p:Principal=Depends(principal_from_key)):
    def find(run_id):
        with SessionLocal() as db:
            r=db.get(Run,run_id)
            if not r:raise HTTPException(404,"run not found")
            return next((t for t in loads(r.payload).get("trials",[]) if t.get("scenario")==req.scenario),None)
    failing=find(req.run_id);passing=find(req.passing_run_id) if req.passing_run_id else None
    if not failing:raise HTTPException(404,"scenario not found")
    return explain_failure(failing,passing)

@app.post('/api/lab/incident')
def import_incident(req:IncidentRequest,p:Principal=Depends(require_role("operator"))):
    faults=[]
    for e in req.events:
        if e.get('fault') and e.get('tool'):faults.append({'tool':e['tool'],'when':{'call':e.get('occurrence',1)},'inject':e['fault'].get('inject',e['fault'])})
    assertions=req.expected_assertions or [{'type':'no_duplicate_tool_args','tool':e.get('tool')} for e in req.events if e.get('tool') and ('refund' in e.get('tool','') or 'transfer' in e.get('tool',''))][:1]
    spec=ScenarioSpec(name=req.name,world=req.world,instruction=req.instruction,variables=req.variables,faults=faults,assertions=assertions,tags=['incident','regression']);saved=upsert(Scenario,spec.name,spec.model_dump(),p.name)
    return {'scenario':saved,'generated_faults':faults,'generated_assertions':assertions,'regression_ready':True}

@app.get('/api/lab/coverage/{run_id}')
def run_coverage(run_id:int,p:Principal=Depends(principal_from_key)):
    with SessionLocal() as db:
        r=db.get(Run,run_id)
        if not r:raise HTTPException(404,'run not found')
        agent=loads(db.get(Agent,r.agent_id).payload);return _coverage(agent,loads(r.payload).get('trials',[]))

# Dataset -> scenarios
@app.post('/api/datasets/{name}/seed-scenarios')
def seed_from_dataset(name:str,world:str,p:Principal=Depends(require_role("operator"))):
    with SessionLocal() as db:
        d=db.execute(select(Dataset).where(Dataset.name==name)).scalar_one_or_none()
        if not d:raise HTTPException(404,"dataset not found")
        rows=loads(d.payload).get("rows",[])
    names=[]
    for i,row in enumerate(rows):
        n=f"{name}-{i+1:04d}"; instruction=row.get("instruction") or row.get("prompt") or dumps(row); spec=ScenarioSpec(name=n,world=world,instruction=instruction,variables=row.get("variables",row),assertions=row.get("assertions",[]),tags=["dataset",name]);upsert(Scenario,n,spec.model_dump(),p.name);names.append(n)
    return {"dataset":name,"created":len(names),"scenarios":names}

# Distributed worker plane
@app.post('/api/enterprise/workers/register')
def worker_register(req:WorkerRegisterRequest,p:Principal=Depends(require_role("operator"))):return register_worker(req.worker_id,req.labels,req.capabilities,req.capacity)
@app.post('/api/enterprise/workers/heartbeat')
def worker_heartbeat(req:WorkerHeartbeatRequest,p:Principal=Depends(principal_from_key)):
    out=heartbeat(req.worker_id,req.active_jobs)
    if not out:raise HTTPException(404,"worker not registered")
    return out
@app.get('/api/enterprise/workers')
def workers(p:Principal=Depends(principal_from_key)):
    with SessionLocal() as db:return [serialize_worker(x) for x in db.execute(select(Worker).order_by(Worker.last_heartbeat.desc())).scalars()]
@app.post('/api/enterprise/jobs/claim')
def job_claim(req:JobClaimRequest,p:Principal=Depends(principal_from_key)):return claim(req.worker_id,req.capabilities) or {"job":None}

@app.post('/api/enterprise/jobs/{job_id}/complete')
def job_complete(job_id:int,req:JobCompleteRequest,p:Principal=Depends(principal_from_key)):
    before=None
    with SessionLocal() as db:
        j=db.get(Job,job_id)
        if j:before=loads(j.payload)
    out=complete(job_id,req.worker_id,req.lease_token,req.result,req.failed)
    if not out:raise HTTPException(409,"invalid job lease")
    if out["status"]=="completed" and out["kind"]=="trial.execute" and before:
        run_id=before.get("run_id")
        with SessionLocal() as db:
            r=db.get(Run,run_id)
            if r:
                pl=loads(r.payload);trials=pl.get("trials",[]);trials.append(req.result);pl.update(_run_payload(trials,pl.get("seed",1),{"distributed":True,"job_ids":pl.get("job_ids",[]),"expected_trials":pl.get("expected_trials",0)}));
                if len(trials)>=pl.get("expected_trials",0):r.status="completed"
                else:r.status="running"
                r.payload=dumps(pl);db.commit()
    return out

@app.get('/api/enterprise/jobs')
def jobs(status:str|None=None,p:Principal=Depends(principal_from_key)):
    with SessionLocal() as db:
        q=select(Job).order_by(Job.id.desc()).limit(500)
        if status:q=q.where(Job.status==status)
        return [serialize_job(x) for x in db.execute(q).scalars()]

@app.post('/api/enterprise/matrix')
async def matrix(req:MatrixRequest,p:Principal=Depends(require_role("operator"))):
    runs=[]
    for agent in req.agents:
        for seed in req.seeds:
            with SessionLocal() as db:
                ar=db.execute(select(Agent).where(Agent.name==agent)).scalar_one_or_none();sr=db.execute(select(Suite).where(Suite.name==req.suite)).scalar_one_or_none()
                if not ar or not sr:continue
                r=Run(name=f"{req.name}:{agent}:s{seed}",agent_id=ar.id,suite_id=sr.id,status="queued",payload=dumps({"matrix":req.name}));db.add(r);db.commit();db.refresh(r);rid=r.id
            if req.distributed:
                scheduled=await schedule_distributed_run(rid,seed,max(1,req.repetitions));runs.append(scheduled)
            else:
                completed=await execute_run(rid,seed,max(1,req.repetitions),settings.max_parallel_trials);runs.append(completed)
    return {"matrix":req.name,"runs":runs,"count":len(runs),"suite":req.suite}

# API keys + audit
@app.post('/api/enterprise/api-keys')
def create_api_key(req:ApiKeyCreateRequest,p:Principal=Depends(require_role("admin"))):
    raw=issue_api_key(req.name,req.role,req.scopes);audit(p.name,"api_key.create",req.name,{"role":req.role,"scopes":req.scopes});return {"name":req.name,"key":raw,"warning":"shown once; store it in your secret manager"}
@app.get('/api/enterprise/audit')
def audit_log(limit:int=200,p:Principal=Depends(require_role("admin"))):
    with SessionLocal() as db:
        rows=db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(1000,max(1,limit)))).scalars();return [{"id":x.id,"actor":x.actor,"action":x.action,"resource":x.resource,"details":loads(x.details),"created_at":x.created_at.isoformat()} for x in rows]

@app.post('/api/demo/seed')
def seed_demo(p:Principal=Depends(require_role("operator"))):
    world=WorldSpec(name="ecommerce-demo",state={"customers":{"c1":{"id":"c1","tier":"gold"}},"orders":{"o1":{"id":"o1","status":"delivered","amount":49.99}},"payments":{"p1":{"id":"p1","order_id":"o1","status":"captured","amount":49.99}},"refunds":{}},schema={"entities":["customers","orders","payments","refunds"]})
    upsert(World,world.name,world.model_dump(by_alias=True),p.name)
    tools=[{"name":"get_payment","mode":"simulated","input_schema":{"type":"object","properties":{"id":{"type":"string"}},"required":["id"]},"output_schema":{"type":"object"},"simulation":{"op":"lookup","collection":"payments","key_arg":"id"}},{"name":"create_refund","mode":"simulated","input_schema":{"type":"object"},"output_schema":{"type":"object","required":["id","payment_id","amount"]},"simulation":{"op":"create","collection":"refunds","id_prefix":"refund"}}]
    agent=AgentSpec(name="refund-agent",version="1.0.1",tools=tools,behavior={"plan":[{"tool":"get_payment","args":{"id":"$payment_id"}},{"tool":"create_refund","args":{"payment_id":"$payment_id","amount":49.99}}],"final_response":"Refund completed"});upsert(Agent,agent.name,agent.model_dump(),p.name)
    s1=ScenarioSpec(name="happy-refund",world=world.name,instruction="Refund the payment",variables={"payment_id":"p1"},assertions=[{"type":"state_exists","path":"refunds.refund_0001"},{"type":"no_duplicate_tool_args","tool":"create_refund"}]);upsert(Scenario,s1.name,s1.model_dump(),p.name)
    s2=ScenarioSpec(name="payment-timeout",world=world.name,instruction="Refund despite transient lookup failure",variables={"payment_id":"p1"},faults=[{"tool":"get_payment","when":{"call":1},"inject":{"error":"timeout"}}],assertions=[{"type":"state_exists","path":"refunds.refund_0001"},{"type":"max_tool_calls","value":8}]);upsert(Scenario,s2.name,s2.model_dump(),p.name)
    suite=SuiteSpec(name="refund-regression",scenarios=[s1.name,s2.name]);upsert(Suite,suite.name,suite.model_dump(),p.name)
    constraint=ConstraintSpec(name="no-double-refund",description="The same refund request may not be issued twice with identical arguments.",severity="critical",assertion={"type":"no_duplicate_tool_args","tool":"create_refund"},bindings={"scenarios":[s1.name,s2.name]});upsert(Constraint,constraint.name,constraint.model_dump(),p.name)
    return {"ok":True,"product":"Ordeal","agent":agent.name,"world":world.name,"suite":suite.name,"constraint":constraint.name}
