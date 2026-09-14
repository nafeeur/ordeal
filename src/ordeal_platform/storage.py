from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path

from .security import Vault

SAFE_KEY = re.compile(r"^[a-f0-9]{32}/[a-f0-9]{64}$")


class ArtifactStore:
    """Tenant-addressed, encrypted, content-addressable payload storage."""
    def __init__(self, settings):
        self.settings = settings
        self.vault = Vault(settings.master_key)
        self.root = settings.data_dir / "artifacts"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.s3 = None
        if settings.s3_bucket:
            import boto3
            self.s3 = boto3.client("s3", endpoint_url=settings.s3_endpoint or None)

    def _validate(self, key):
        if not SAFE_KEY.fullmatch(key):
            raise ValueError("Invalid artifact key")

    def put(self, tenant_id: str, payload: bytes) -> tuple[str, str]:
        checksum = hashlib.sha256(payload).hexdigest()
        key = f"{tenant_id}/{checksum}"
        self._validate(key)
        encrypted = self.vault.encrypt_bytes(payload, key)
        if self.s3:
            self.s3.put_object(Bucket=self.settings.s3_bucket, Key=key, Body=encrypted,
                               ContentType="application/octet-stream", ServerSideEncryption="AES256")
        else:
            path = self.root / key
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encrypted)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        return key, checksum

    def get(self, key: str) -> bytes:
        self._validate(key)
        blob = self.s3.get_object(Bucket=self.settings.s3_bucket, Key=key)["Body"].read() if self.s3 else (self.root / key).read_bytes()
        plain = self.vault.decrypt_bytes(blob, key)
        if hashlib.sha256(plain).hexdigest() != key.split("/")[1]:
            raise ValueError("Artifact digest mismatch")
        return plain

    def delete(self, key: str):
        self._validate(key)
        if self.s3:
            self.s3.delete_object(Bucket=self.settings.s3_bucket, Key=key)
        else:
            (self.root / key).unlink(missing_ok=True)
