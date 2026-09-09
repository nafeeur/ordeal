#!/usr/bin/env python3
"""Ordeal Kafka execution worker.

Consumes Apache Kafka trial topics via consumer groups. Run multiple CPU worker
replicas for horizontal scale; model inference is accessed through external HTTP endpoints.
"""
import argparse, asyncio, json, os, socket, time
import httpx
from aiokafka import AIOKafkaConsumer
from app.engine import SimulationEngine, evaluate_assertions, run_mock_agent, stable_hash
from app.agent_runtime import run_openai_compatible
from app.kafka import make_producer, result_topic, topic_for_capability, json_bytes
from app.settings import settings

async def execute(payload):
    agent,scenario,world,seed=payload["agent"],payload["scenario"],payload["world"],int(payload.get("seed",1))
    sim=SimulationEngine(); trial=sim.start_trial(world.get("state",{}),agent.get("tools",[]),scenario.get("faults",[]),seed,scenario)
    for setup in scenario.get("setup",[]): trial.ledger.commit(setup.get("op","set"),setup.get("path",""),setup.get("value"),source="scenario.setup")
    if agent.get("dispatch_type")=="openai_compatible": final=await run_openai_compatible(agent,scenario,trial,sim,seed)
    elif agent.get("dispatch_type")=="http":
        async with httpx.AsyncClient(timeout=float(scenario.get("timeout_seconds",120))) as c:
            r=await c.post(agent["endpoint"],json={"trial_id":trial.id,"instruction":scenario.get("instruction","")});r.raise_for_status();b=r.json();final=b.get("final_response",b.get("output",""))
    else: final=await run_mock_agent(agent,scenario,trial,sim,seed)
    ledger=trial.ledger.export();grades=evaluate_assertions(trial.state,trial.events,scenario.get("assertions",[]),ledger)
    return {"id":trial.id,"scenario":scenario.get("name"),"seed":seed,"passed":all(g["passed"] for g in grades) if grades else True,"final_response":final,"initial_state":ledger["initial_state"],"final_state":trial.state,"initial_hash":ledger["initial_hash"],"final_hash":ledger["final_hash"],"events":trial.events,"ledger":ledger,"grades":grades,"scenario_snapshot":scenario,"world_snapshot":world,"agent_snapshot":agent,"execution_fingerprint":stable_hash({"agent":agent.get("name"),"scenario":scenario,"world":world,"seed":seed})}

async def heartbeat_loop(api,headers,worker_id,running):
    async with httpx.AsyncClient(base_url=api,headers=headers,timeout=30) as client:
        while True:
            try: await client.post('/api/enterprise/workers/heartbeat',json={"worker_id":worker_id,"active_jobs":len(running)})
            except Exception: pass
            await asyncio.sleep(10)

async def main():
    ap=argparse.ArgumentParser();ap.add_argument("--api",default=os.getenv("ORDEAL_API_URL","http://localhost:8000"));ap.add_argument("--key",default=os.getenv("ORDEAL_API_KEY"));ap.add_argument("--worker-id",default=f"{socket.gethostname()}-{os.getpid()}");ap.add_argument("--capabilities",default="cpu",help="comma-separated logical worker capabilities; default: cpu");ap.add_argument("--capacity",type=int,default=1);ap.add_argument("--labels",default="");args=ap.parse_args()
    headers={"X-Ordeal-Key":args.key} if args.key else {}
    caps=[x.strip() for x in args.capabilities.split(',') if x.strip()] or ["cpu"]
    labels={}
    for pair in filter(None,args.labels.split(',')):
        k,_,v=pair.partition('=');labels[k]=v
    async with httpx.AsyncClient(base_url=args.api,headers=headers,timeout=30) as client:
        r=await client.post('/api/enterprise/workers/register',json={"worker_id":args.worker_id,"labels":labels,"capabilities":caps,"capacity":args.capacity});r.raise_for_status()

    topics=sorted({topic_for_capability(c) for c in caps} | ({topic_for_capability("any")} if "any" not in caps else set()))
    group=f"{settings.kafka_consumer_group_prefix}-workers-{'-'.join(sorted(caps))}"
    consumer=AIOKafkaConsumer(*topics,bootstrap_servers=settings.kafka_bootstrap_servers,group_id=group,enable_auto_commit=False,auto_offset_reset="earliest",max_poll_records=max(1,args.capacity*2))
    producer=await make_producer(); await consumer.start()
    sem=asyncio.Semaphore(max(1,args.capacity)); running=set(); hb=asyncio.create_task(heartbeat_loop(args.api,headers,args.worker_id,running))

    async def handle(msg):
        async with sem:
            event=json.loads(msg.value); failed=False
            try: result=await execute(event["payload"])
            except Exception as exc: result={"error":type(exc).__name__,"detail":str(exc)}; failed=True
            result_event={"schema":"ordeal.result.v1","job_id":event["job_id"],"attempt":event.get("attempt",1),"worker_id":args.worker_id,"failed":failed,"result":result,"completed_at":time.time()}
            await producer.send_and_wait(result_topic(),json_bytes(result_event),key=str(event["job_id"]).encode())
            # Commit only after the durable result event is acknowledged by Kafka.
            await consumer.commit()

    try:
        async for msg in consumer:
            while len(running)>=args.capacity:
                done,_=await asyncio.wait(running,return_when=asyncio.FIRST_COMPLETED)
                running.difference_update(done)
            t=asyncio.create_task(handle(msg));running.add(t);t.add_done_callback(lambda x: running.discard(x))
    finally:
        hb.cancel(); await consumer.stop(); await producer.stop()

if __name__=='__main__': asyncio.run(main())
