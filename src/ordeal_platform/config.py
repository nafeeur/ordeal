from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def env_value(name: str, default: str = "") -> str:
    """Read a value or an explicitly mounted secret file, never both."""
    value, source = os.environ.get(name), os.environ.get(name + "_FILE")
    if value is not None and source:
        raise ValueError(f"Set either {name} or {name}_FILE, not both")
    if source:
        path = Path(source)
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise ValueError(f"{name}_FILE must be a regular file smaller than 1 MiB")
        return path.read_text(encoding="utf-8").strip()
    return default if value is None else value


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./.ordeal/platform.db"
    data_dir: Path = Path(".ordeal")
    master_key: str = ""
    public_url: str = "http://127.0.0.1:8080"
    production: bool = False
    max_body_bytes: int = 4 * 1024 * 1024
    max_trace_events: int = 10000
    outbound_hosts: tuple[str, ...] = ()
    allow_private_outbound: bool = False
    request_limit: int = 300
    lease_seconds: int = 90
    oidc: dict = field(default_factory=dict)
    s3_bucket: str = ""
    s3_endpoint: str = ""
    encryption_key_id: str = "local-v1"

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.getenv("ORDEAL_DATA_DIR", ".ordeal"))
        key = env_value("ORDEAL_MASTER_KEY")
        key_file = root / "master.key"
        if not key and key_file.is_file():
            key = key_file.read_text().strip()
        return cls(
            database_url=env_value("ORDEAL_DATABASE_URL", f"sqlite:///{root}/platform.db"),
            data_dir=root,
            master_key=key,
            public_url=os.getenv("ORDEAL_PUBLIC_URL", "http://127.0.0.1:8080").rstrip("/"),
            production=os.getenv("ORDEAL_PRODUCTION", "0") == "1",
            outbound_hosts=tuple(h.strip().lower() for h in os.getenv("ORDEAL_OUTBOUND_HOSTS", "").split(",") if h.strip()),
            allow_private_outbound=os.getenv("ORDEAL_ALLOW_PRIVATE_OUTBOUND", "0") == "1",
            oidc=json.loads(env_value("ORDEAL_OIDC_CONFIG", "{}")),
            s3_bucket=os.getenv("ORDEAL_S3_BUCKET", ""),
            s3_endpoint=os.getenv("ORDEAL_S3_ENDPOINT", ""),
            encryption_key_id=os.getenv("ORDEAL_ENCRYPTION_KEY_ID", "local-v1"),
            max_body_bytes=int(os.getenv("ORDEAL_MAX_BODY_BYTES", "4194304")),
            max_trace_events=int(os.getenv("ORDEAL_MAX_TRACE_EVENTS", "10000")),
            request_limit=int(os.getenv("ORDEAL_REQUEST_LIMIT", "300")),
            lease_seconds=int(os.getenv("ORDEAL_LEASE_SECONDS", "90")),
        )

    def validate(self) -> None:
        try:
            raw = base64.urlsafe_b64decode(self.master_key)
        except Exception as exc:
            raise ValueError("ORDEAL_MASTER_KEY must be a URL-safe base64 encoded 32-byte key") from exc
        if len(raw) != 32:
            raise ValueError("Run ordeal-server init first or supply ORDEAL_MASTER_KEY (32 random bytes, base64)")
        if self.production and not self.public_url.startswith("https://"):
            raise ValueError("Production requires an HTTPS ORDEAL_PUBLIC_URL and a TLS reverse proxy")
        if self.production and self.allow_private_outbound:
            raise ValueError("Private outbound override is a development-only option")
        if self.max_body_bytes < 1024 or self.request_limit < 1 or not 1 <= self.lease_seconds <= 3600 or self.max_trace_events < 1:
            raise ValueError("Invalid request limits")
