#!/usr/bin/env python3
"""Transactional-outbox dispatcher: Postgres -> Apache Kafka.

This service is deliberately tiny and stateless. Multiple replicas may run; on
PostgreSQL they use SKIP LOCKED to divide pending outbox rows safely.
"""
import asyncio, json
from datetime import datetime
from sqlalchemy import select
from app.db import SessionLocal
from app.models import OutboxEvent
from app.jobs import loads, build_dispatch_envelope, mark_dispatched
from app.kafka import make_producer, json_bytes
from app.settings import settings

async def main():
    producer=await make_producer()
    try:
        while True:
            rows=[]
            with SessionLocal() as db:
                stmt=(select(OutboxEvent)
                      .where(OutboxEvent.status=="pending")
                      .order_by(OutboxEvent.id.asc())
                      .limit(settings.kafka_outbox_batch))
                if db.bind and db.bind.dialect.name=="postgresql": stmt=stmt.with_for_update(skip_locked=True)
                rows=list(db.execute(stmt).scalars())
                # Mark in-flight in one short transaction; crashed rows are reset by timeout-free recovery below.
                for row in rows:
                    row.status="publishing"; row.attempts += 1
                db.commit()
            if not rows:
                # Recover stale publishing rows conservatively. Duplicate Kafka delivery is safe/idempotent.
                with SessionLocal() as db:
                    stale=list(db.execute(select(OutboxEvent).where(OutboxEvent.status=="publishing").limit(settings.kafka_outbox_batch)).scalars())
                    for row in stale: row.status="pending"
                    if stale: db.commit()
                await asyncio.sleep(.1)
                continue
            for row in rows:
                try:
                    raw=loads(row.payload); job_id=int(raw["job_id"])
                    with SessionLocal() as db:
                        envelope=build_dispatch_envelope(db,job_id)
                    if envelope is None:
                        with SessionLocal() as db:
                            current=db.get(OutboxEvent,row.id); current.status="published"; current.published_at=datetime.utcnow(); db.commit()
                        continue
                    await producer.send_and_wait(row.topic,json_bytes(envelope),key=row.event_key.encode())
                    with SessionLocal() as db:
                        current=db.get(OutboxEvent,row.id); current.status="published"; current.published_at=datetime.utcnow(); current.last_error=None
                        mark_dispatched(db,job_id); db.commit()
                except Exception as exc:
                    with SessionLocal() as db:
                        current=db.get(OutboxEvent,row.id); current.status="pending"; current.last_error=f"{type(exc).__name__}: {exc}"; db.commit()
                    await asyncio.sleep(.2)
    finally:
        await producer.stop()

if __name__=="__main__": asyncio.run(main())
