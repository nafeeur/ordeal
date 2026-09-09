import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}

@dataclass(frozen=True)
class Settings:
    environment: str = os.getenv("ORDEAL_ENV", "development")
    auth_disabled: bool = _bool("ORDEAL_AUTH_DISABLED", True)
    cors_origins: str = os.getenv("ORDEAL_CORS_ORIGINS", "http://localhost:3000")
    public_base_url: str = os.getenv("ORDEAL_PUBLIC_BASE_URL", "http://localhost:8000")
    tool_proxy_base_url: str = os.getenv("ORDEAL_TOOL_PROXY_BASE_URL", "http://localhost:8000")
    max_parallel_trials: int = int(os.getenv("ORDEAL_MAX_PARALLEL_TRIALS", "16"))
    max_fuzz_iterations: int = int(os.getenv("ORDEAL_MAX_FUZZ_ITERATIONS", "5000"))
    job_lease_seconds: int = int(os.getenv("ORDEAL_JOB_LEASE_SECONDS", "120"))
    allow_command_agents: bool = _bool("ORDEAL_ALLOW_COMMAND_AGENTS", False)
    simulator_endpoint: str | None = os.getenv("ORDEAL_SIMULATOR_ENDPOINT")
    simulator_model: str | None = os.getenv("ORDEAL_SIMULATOR_MODEL")
    simulator_api_key: str | None = os.getenv("ORDEAL_SIMULATOR_API_KEY")
    retention_days: int = int(os.getenv("ORDEAL_RETENTION_DAYS", "30"))
    queue_backend: str = os.getenv("ORDEAL_QUEUE_BACKEND", "kafka")
    kafka_bootstrap_servers: str = os.getenv("ORDEAL_KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    kafka_topic_prefix: str = os.getenv("ORDEAL_KAFKA_TOPIC_PREFIX", "ordeal")
    kafka_consumer_group_prefix: str = os.getenv("ORDEAL_KAFKA_CONSUMER_GROUP_PREFIX", "ordeal")
    kafka_compression: str = os.getenv("ORDEAL_KAFKA_COMPRESSION", "lz4")
    kafka_linger_ms: int = int(os.getenv("ORDEAL_KAFKA_LINGER_MS", "5"))
    kafka_max_batch_bytes: int = int(os.getenv("ORDEAL_KAFKA_MAX_BATCH_BYTES", "1048576"))
    kafka_security_protocol: str | None = os.getenv("ORDEAL_KAFKA_SECURITY_PROTOCOL")
    kafka_sasl_mechanism: str | None = os.getenv("ORDEAL_KAFKA_SASL_MECHANISM")
    kafka_sasl_username: str | None = os.getenv("ORDEAL_KAFKA_SASL_USERNAME")
    kafka_sasl_password: str | None = os.getenv("ORDEAL_KAFKA_SASL_PASSWORD")
    kafka_outbox_batch: int = int(os.getenv("ORDEAL_KAFKA_OUTBOX_BATCH", "500"))

settings = Settings()
