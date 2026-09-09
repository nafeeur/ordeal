import json, secrets
from datetime import datetime, timedelta
from sqlalchemy import select
from .db import SessionLocal
from .models import Job, Worker, OutboxEvent, Run
from .settings import settings
from .kafka import topic_for_capability


def dumps(x): return json.dumps(x,separators=(",",":"),default=str)
def loads(x): return json.loads(x or "{}")

def _outbox(db, topic: str, key: str, payload: dict):
    db.add(OutboxEvent(topic=topic,event_key=key,payload=dumps(payload),status="pending"))


def enqueue(kind: str, payload: dict, capability: str = "cpu", priority: int = 100, max_attempts: int = 3):
    """Persist job + Kafka outbox event atomically.

    The dispatcher publishes the outbox asynchronously. This avoids losing jobs if
    Kafka is briefly unavailable after the API has committed the run metadata.
    """
    with SessionLocal() as db:
        j=Job(kind=kind,status="queued",required_capability=capability,priority=priority,payload=dumps(payload),max_attempts=max_attempts)
        db.add(j); db.flush()
        _outbox(db, topic_for_capability(capability), str(j.id), {"job_id":j.id})
        db.commit(); db.refresh(j); return serialize_job(j)


def register_worker(worker_id: str, labels: dict, capabilities: list[str], capacity: int):
    with SessionLocal() as db:
        w=db.execute(select(Worker).where(Worker.worker_id==worker_id)).scalar_one_or_none()
        if not w:
            w=Worker(worker_id=worker_id); db.add(w)
        w.labels=dumps(labels); w.capabilities=dumps(capabilities); w.capacity=max(1,capacity); w.status="online"; w.last_heartbeat=datetime.utcnow(); db.commit(); db.refresh(w); return serialize_worker(w)


def heartbeat(worker_id: str, active_jobs: int | None = None):
    with SessionLocal() as db:
        w=db.execute(select(Worker).where(Worker.worker_id==worker_id)).scalar_one_or_none()
        if not w: return None
        w.last_heartbeat=datetime.utcnow(); w.status="online"
        if active_jobs is not None: w.active_jobs=max(0,active_jobs)
        db.commit(); db.refresh(w); return serialize_worker(w)


def claim(worker_id: str, capabilities: list[str]):
    """Legacy DB-polling claim path for development/backward compatibility.

    Production workers consume Kafka directly and do not use this method.
    """
    now=datetime.utcnow(); lease=now+timedelta(seconds=settings.job_lease_seconds)
    with SessionLocal() as db:
        expired=db.execute(select(Job).where(Job.status=="leased",Job.lease_expires_at < now)).scalars().all()
        for j in expired:
            j.status="queued"; j.worker_id=None; j.lease_token=None; j.lease_expires_at=None
        rows=db.execute(select(Job).where(Job.status=="queued").order_by(Job.priority.asc(),Job.id.asc()).limit(50)).scalars().all()
        chosen=next((j for j in rows if j.required_capability in capabilities or j.required_capability=="any"),None)
        if not chosen: db.commit(); return None
        chosen.status="leased"; chosen.worker_id=worker_id; chosen.lease_token=secrets.token_hex(16); chosen.lease_expires_at=lease; chosen.attempts+=1
        db.commit(); db.refresh(chosen); return serialize_job(chosen)


def complete(job_id:int, worker_id:str, lease_token:str, result:dict, failed:bool=False):
    """Legacy HTTP completion path. Kafka aggregator uses apply_kafka_result."""
    with SessionLocal() as db:
        j=db.get(Job,job_id)
        if not j or j.worker_id!=worker_id or j.lease_token!=lease_token: return None
        if failed and j.attempts<j.max_attempts:
            j.status="queued"; j.result=dumps(result); j.worker_id=None; j.lease_token=None; j.lease_expires_at=None
            _outbox(db, topic_for_capability(j.required_capability), str(j.id), {"job_id":j.id})
        else:
            j.status="failed" if failed else "completed"; j.result=dumps(result); j.lease_expires_at=None
        db.commit(); db.refresh(j); return serialize_job(j)


def build_dispatch_envelope(db, job_id: int, worker_attempt: int | None = None) -> dict | None:
    j=db.get(Job,job_id)
    if not j or j.status in {"completed","failed"}: return None
    attempt = worker_attempt if worker_attempt is not None else j.attempts + 1
    return {
        "schema":"ordeal.job.v1",
        "job_id":j.id,
        "kind":j.kind,
        "required_capability":j.required_capability,
        "priority":j.priority,
        "attempt":attempt,
        "max_attempts":j.max_attempts,
        "payload":loads(j.payload),
        "created_at":j.created_at.isoformat(),
    }


def mark_dispatched(db, job_id:int):
    j=db.get(Job,job_id)
    if not j or j.status in {"completed","failed"}: return
    j.status="dispatched"; j.attempts += 1; j.lease_token=None; j.lease_expires_at=None


def apply_kafka_result(job_id:int, worker_id:str, result:dict, failed:bool=False):
    """Idempotently persist a Kafka result and enqueue retries via outbox."""
    with SessionLocal() as db:
        j=db.get(Job,job_id)
        if not j: return {"status":"missing","job_id":job_id}
        if j.status in {"completed","failed"}:
            return {"status":"duplicate","job":serialize_job(j)}
        j.worker_id=worker_id; j.result=dumps(result)
        if failed and j.attempts < j.max_attempts:
            j.status="queued"
            _outbox(db, topic_for_capability(j.required_capability), str(j.id), {"job_id":j.id,"retry":True})
            terminal=False
        else:
            j.status="failed" if failed else "completed"; terminal=True
        db.commit(); db.refresh(j)
        return {"status":"applied","terminal":terminal,"job":serialize_job(j),"payload":loads(j.payload)}


def serialize_job(j):
    return {"id":j.id,"kind":j.kind,"status":j.status,"required_capability":j.required_capability,"priority":j.priority,"payload":loads(j.payload),"result":loads(j.result),"worker_id":j.worker_id,"lease_token":j.lease_token,"lease_expires_at":j.lease_expires_at.isoformat() if j.lease_expires_at else None,"attempts":j.attempts,"max_attempts":j.max_attempts,"created_at":j.created_at.isoformat()}
def serialize_worker(w):
    return {"worker_id":w.worker_id,"labels":loads(w.labels),"capabilities":json.loads(w.capabilities or "[]"),"status":w.status,"capacity":w.capacity,"active_jobs":w.active_jobs,"last_heartbeat":w.last_heartbeat.isoformat()}

def aggregate_trial_into_run(run_id:int, trial:dict):
    """Idempotently aggregate a terminal trial result into its run."""
    from .engine import stable_hash
    from .constraints import stability_report, constraint_summary
    with SessionLocal() as db:
        r=db.get(Run,run_id)
        if not r: return {"status":"missing_run","run_id":run_id}
        pl=loads(r.payload); trials=pl.get("trials",[])
        identity=(trial.get("scenario"), trial.get("seed"), trial.get("execution_fingerprint"))
        if any((t.get("scenario"),t.get("seed"),t.get("execution_fingerprint"))==identity for t in trials):
            return {"status":"duplicate","run_id":run_id}
        trials.append(trial)
        pc=sum(1 for t in trials if t.get("passed"))
        expected=int(pl.get("expected_trials",0))
        pl.update({
            "trials":trials,
            "pass_count":pc,
            "fail_count":len(trials)-pc,
            "pass_rate":pc/len(trials) if trials else 0,
            "run_fingerprint":stable_hash([{"scenario":t.get("scenario"),"seed":t.get("seed"),"final_hash":t.get("final_hash"),"passed":t.get("passed")} for t in trials]),
            "stability":stability_report(trials),
            "constraint_summary":constraint_summary(trials),
        })
        r.status="completed" if expected and len(trials)>=expected else "running"
        r.payload=dumps(pl); db.commit()
        return {"status":"aggregated","run_id":run_id,"completed":r.status=="completed","trials":len(trials),"expected":expected}
