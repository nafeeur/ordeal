"""Kafka transport for Ordeal's high-volume execution data plane.

Application services remain Python. Apache Kafka is the durable streaming backbone.
Delivery semantics are at-least-once; job/result handlers are idempotent by job id.
"""
from __future__ import annotations
import json
from typing import Any
from .settings import settings


def topic_for_capability(capability: str) -> str:
    cap = (capability or "cpu").lower().replace("/", "-").replace(" ", "-")
    return f"{settings.kafka_topic_prefix}.jobs.{cap}"


def result_topic() -> str:
    return f"{settings.kafka_topic_prefix}.results"


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), default=str).encode("utf-8")


async def make_producer():
    from aiokafka import AIOKafkaProducer
    kwargs = {
        "bootstrap_servers": settings.kafka_bootstrap_servers,
        "acks": "all",
        "enable_idempotence": True,
        "compression_type": settings.kafka_compression,
        "max_batch_size": settings.kafka_max_batch_bytes,
        "linger_ms": settings.kafka_linger_ms,
    }
    if settings.kafka_security_protocol:
        kwargs["security_protocol"] = settings.kafka_security_protocol
    if settings.kafka_sasl_mechanism:
        kwargs["sasl_mechanism"] = settings.kafka_sasl_mechanism
        kwargs["sasl_plain_username"] = settings.kafka_sasl_username
        kwargs["sasl_plain_password"] = settings.kafka_sasl_password
    p = AIOKafkaProducer(**kwargs)
    await p.start()
    return p
