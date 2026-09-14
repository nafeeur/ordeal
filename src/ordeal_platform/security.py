from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets as random_secrets
import time
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Request

from . import db

ROLE_PERMISSIONS = {
    "owner": {"*"},
    "admin": {"read", "write", "execute", "review", "export", "identity", "audit", "secret", "runner", "billing", "scim"},
    "developer": {"read", "write", "execute", "export", "review"},
    "reviewer": {"read", "review"},
    "viewer": {"read"},
    "billing": {"billing"},
    "runner": {"runner", "read", "execute"},
}
PRIVILEGED_KINDS = {"connector": "secret", "automation": "identity", "monitor": "write"}


def fail(status: int, message: str):
    raise HTTPException(status_code=status, detail=message)


@dataclass(frozen=True)
class Identity:
    id: str
    tenant_id: str
    role: str
    kind: str
    name: str
    projects: tuple[str, ...]
    permissions: frozenset[str]
    token_id: str = ""

    def require(self, permission: str, project_id: str | None = None):
        if "*" not in self.permissions and permission not in self.permissions:
            fail(403, f"Permission required: {permission}")
        if project_id and self.projects and project_id not in self.projects:
            # Do not disclose existence of resources in other projects.
            fail(404, "Project not found")
        return self

    def allow_project(self, project_id: str):
        if self.projects and project_id not in self.projects:
            fail(404, "Project not found")

    def require_org(self, permission: str):
        self.require(permission)
        if self.projects:
            fail(403, "Organization-wide credential required")
        return self


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(conn, principal: dict, label: str = "api", days: float = 90) -> tuple[str, dict]:
    token = "ord_" + random_secrets.token_urlsafe(32)
    entry = dict(id=db.uid(), tenant_id=principal["tenant_id"], principal_id=principal["id"],
                 token_hash=hash_token(token), label=label, expires_at=time.time() + days * 86400,
                 revoked=False, created_at=time.time())
    conn.execute(db.tokens.insert().values(**entry))
    return token, {k: v for k, v in entry.items() if k != "token_hash"}


def authenticate(database: db.Database, token: str) -> Identity:
    if not token or len(token) > 1024:
        fail(401, "Valid bearer credential required")
    with database.tx() as conn:
        row = db.row(conn, sa.select(db.tokens).where(db.tokens.c.token_hash == hash_token(token)))
        if not row or row["revoked"] or row["expires_at"] <= time.time():
            fail(401, "Credential expired, revoked, or invalid")
        p = db.row(conn, db.scoped(db.principals, row["tenant_id"]).where(db.principals.c.id == row["principal_id"]))
        if not p or not p["active"]:
            fail(401, "Identity inactive")
        permissions = set(ROLE_PERMISSIONS.get(p["role"], set()))
        if p["permissions"]:
            permissions = set(p["permissions"]) if "*" in permissions else permissions.intersection(p["permissions"])
        return Identity(p["id"], p["tenant_id"], p["role"], p["kind"], p["name"], tuple(p["projects"]), frozenset(permissions), row["id"])


def identity(request: Request) -> Identity:
    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else request.cookies.get("ordeal_session", "")
    return authenticate(request.app.state.database, token)


class Vault:
    """AES-256-GCM; additional authenticated data binds each record to its tenant and purpose."""
    def __init__(self, key: str):
        self.aes = AESGCM(base64.urlsafe_b64decode(key))

    def encrypt_bytes(self, value: bytes, context: str) -> bytes:
        nonce = os.urandom(12)
        return nonce + self.aes.encrypt(nonce, value, context.encode())

    def decrypt_bytes(self, value: bytes, context: str) -> bytes:
        return self.aes.decrypt(value[:12], value[12:], context.encode())

    def encrypt(self, value: str, context: str) -> str:
        return base64.urlsafe_b64encode(self.encrypt_bytes(value.encode(), context)).decode()

    def decrypt(self, value: str, context: str) -> str:
        return self.decrypt_bytes(base64.urlsafe_b64decode(value), context).decode()


from ordeal_agent.privacy import redact


def verify_hmac(secret: str, timestamp: str, payload: bytes, signature: str, tolerance: int = 300) -> bool:
    try:
        if abs(time.time() - int(timestamp)) > tolerance:
            return False
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.removeprefix("sha256="))
