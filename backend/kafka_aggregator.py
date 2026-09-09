#!/usr/bin/env python3
"""Apache Kafka result consumer -> durable Ordeal metadata/run aggregation."""
import asyncio, json, os, socket
from aiokafka import AIOKafkaConsumer
from app.kafka import result_topic
from app.settings import settings
from app.jobs import apply_kafka_result, aggregate_trial_into_run

async def main():
    group=f"{settings.kafka_consumer_group_prefix}-result-aggregators"
    consumer=AIOKafkaConsumer(
        result_topic(),
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=group,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_poll_records=500,
    )
    await consumer.start()
    try:
        async for msg in consumer:
            event=json.loads(msg.value)
            job_id=int(event["job_id"]); failed=bool(event.get("failed")); result=event.get("result",{})
            applied=apply_kafka_result(job_id,event.get("worker_id","kafka-worker"),result,failed)
            if applied.get("status")=="applied" and applied.get("terminal") and not failed:
                payload=applied.get("payload",{})
                if payload.get("run_id") and result:
                    aggregate_trial_into_run(int(payload["run_id"]),result)
            await consumer.commit()
    finally:
        await consumer.stop()

if __name__=="__main__": asyncio.run(main())
