import hashlib, hmac, json, secrets
from dataclasses import dataclass
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from .db import SessionLocal
from .models import ApiKey, AuditLog
from .settings import settings

ROLE_LEVEL = {"viewer": 10, "operator": 20, "admin": 30}

@dataclass
class Principal:
    name: str
    role: str
    scopes: list[str]


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def issue_api_key(name: str, role: str = "operator", scopes: list[str] | None = None):
    raw = "ord_" + secrets.token_urlsafe(32)
    with SessionLocal() as db:
        row = ApiKey(name=name, prefix=raw[:12], key_hash=hash_key(raw), role=role, scopes=json.dumps(scopes or ["*"]))
        db.add(row); db.commit()
    return raw


def principal_from_key(x_ordeal_key: str | None = Header(default=None)) -> Principal:
    if settings.auth_disabled:
        return Principal("development", "admin", ["*"])
    if not x_ordeal_key:
        raise HTTPException(401, "missing X-Ordeal-Key")
    digest = hash_key(x_ordeal_key)
    with SessionLocal() as db:
        row = db.execute(select(ApiKey).where(ApiKey.key_hash == digest, ApiKey.enabled == True)).scalar_one_or_none()
        if not row or not hmac.compare_digest(row.key_hash, digest):
            raise HTTPException(401, "invalid API key")
        return Principal(row.name, row.role, json.loads(row.scopes or "[]"))


def require_role(role: str):
    def dep(p: Principal = Depends(principal_from_key)):
        if ROLE_LEVEL.get(p.role, 0) < ROLE_LEVEL.get(role, 999):
            raise HTTPException(403, f"requires {role} role")
        return p
    return dep


def audit(actor: str, action: str, resource: str = "", details: dict | None = None):
    with SessionLocal() as db:
        db.add(AuditLog(actor=actor, action=action, resource=resource, details=json.dumps(details or {}, default=str)))
        db.commit()
