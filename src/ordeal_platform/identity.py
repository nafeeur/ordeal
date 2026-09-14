from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

import jwt
import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from . import db, schemas
from .network import json_request, request as net_request, validate_url
from .security import Identity, authenticate, fail, identity, issue_token, hash_token

router = APIRouter()


def cookie(response, token, settings, max_age=28800):
    response.set_cookie("ordeal_session", token, httponly=True, secure=settings.production or settings.public_url.startswith("https://"),
                        samesite="strict", max_age=max_age, path="/")
    return response


@router.post("/auth/session")
def login(body: schemas.SessionLogin, request: Request):
    ident = authenticate(request.app.state.database, body.token)
    with request.app.state.database.tx(ident.tenant_id) as conn:
        principal = db.row(conn, db.scoped(db.principals, ident.tenant_id).where(db.principals.c.id == ident.id))
        count = conn.execute(sa.select(sa.func.count()).select_from(db.tokens).where(db.tokens.c.principal_id == ident.id,
            db.tokens.c.revoked == False, db.tokens.c.expires_at > time.time(), db.tokens.c.label == "browser-session")).scalar()
        if count >= 30:
            fail(429, "Too many browser sessions; revoke old sessions first")
        token, _ = issue_token(conn, principal, "browser-session", 1/3)
        db.audit_event(conn, ident.tenant_id, ident.id, "session.created")
    response = JSONResponse({"authenticated": True})
    return cookie(response, token, request.app.state.settings)


@router.post("/auth/logout")
def logout(request: Request, ident: Identity = Depends(identity)):
    with request.app.state.database.tx(ident.tenant_id) as conn:
        conn.execute(db.tokens.update().where(db.tokens.c.id == ident.token_id, db.tokens.c.tenant_id == ident.tenant_id).values(revoked=True))
        db.audit_event(conn, ident.tenant_id, ident.id, "session.revoked", ident.token_id)
    response = JSONResponse({"authenticated": False})
    response.delete_cookie("ordeal_session", path="/")
    return response


@router.get("/auth/config")
def auth_config(request: Request):
    return {"oidc_enabled": bool(request.app.state.settings.oidc), "password_login": False}


@router.get("/auth/oidc/start")
def oidc_start(request: Request):
    settings = request.app.state.settings
    cfg = settings.oidc
    if not cfg:
        fail(404, "OIDC is not configured")
    for field in ("issuer", "client_id", "authorization_endpoint", "token_endpoint", "jwks_uri", "tenant_id"):
        if not cfg.get(field):
            fail(503, "OIDC configuration is incomplete")
    validate_url(cfg["authorization_endpoint"], settings)
    state, nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with request.app.state.database.tx() as conn:
        conn.execute(db.login_states.delete().where(db.login_states.c.expires_at < time.time()))
        conn.execute(db.login_states.insert().values(id=state, expires_at=time.time() + 600, data={
            "nonce": nonce, "verifier": request.app.state.vault.encrypt(verifier, "oidc:" + state)}))
    query = urlencode({"response_type": "code", "client_id": cfg["client_id"], "redirect_uri": settings.public_url + "/auth/oidc/callback",
        "scope": "openid profile email", "state": state, "nonce": nonce, "code_challenge": challenge, "code_challenge_method": "S256"})
    response = RedirectResponse(cfg["authorization_endpoint"] + ("&" if "?" in cfg["authorization_endpoint"] else "?") + query)
    # State is bound to this browser as well as to the server-side PKCE transaction.
    # Lax is necessary for the top-level redirect back from an external IdP.
    response.set_cookie("ordeal_oidc_state", state, httponly=True, samesite="lax", max_age=600,
                        secure=settings.production or settings.public_url.startswith("https://"), path="/auth/oidc")
    return response


@router.get("/auth/oidc/callback")
def oidc_callback(request: Request, state: str = "", code: str = "", error: str | None = None):
    settings = request.app.state.settings
    cfg = settings.oidc
    if not cfg or error or not code or len(code) > 4096 or len(state) > 128:
        fail(400, "OIDC authorization failed")
    browser_state = request.cookies.get("ordeal_oidc_state", "")
    if not browser_state or not secrets.compare_digest(browser_state, state):
        fail(400, "OIDC state does not belong to this browser")
    with request.app.state.database.tx() as conn:
        saved = db.row(conn, sa.select(db.login_states).where(db.login_states.c.id == state))
        if not saved or saved["expires_at"] < time.time():
            fail(400, "OIDC state expired or invalid")
        conn.execute(db.login_states.delete().where(db.login_states.c.id == state))
    verifier = request.app.state.vault.decrypt(saved["data"]["verifier"], "oidc:" + state)
    form = {"grant_type": "authorization_code", "client_id": cfg["client_id"], "code": code,
            "redirect_uri": settings.public_url + "/auth/oidc/callback", "code_verifier": verifier}
    if cfg.get("client_secret"):
        form["client_secret"] = cfg["client_secret"]
    try:
        status, _, body = net_request(settings, "POST", cfg["token_endpoint"], body=urlencode(form).encode(),
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        if status != 200:
            fail(401, "OIDC token exchange rejected")
        id_token = json.loads(body)["id_token"]
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") not in {"RS256", "ES256"}:
            fail(401, "OIDC signing algorithm not allowed")
        keyset = json_request(settings, "GET", cfg["jwks_uri"])
        keys = [item for item in keyset.get("keys", []) if item.get("kid") == header.get("kid") and item.get("use", "sig") == "sig"]
        if len(keys) != 1:
            fail(401, "OIDC signing key is ambiguous or missing")
        signing_key = jwt.PyJWK.from_dict(keys[0]).key
        claims = jwt.decode(id_token, signing_key, algorithms=[header["alg"]], audience=cfg["client_id"], issuer=cfg["issuer"],
                            options={"require": ["exp", "iat", "sub", "aud", "iss", "nonce"]}, leeway=30)
        if claims["nonce"] != saved["data"]["nonce"]:
            fail(401, "OIDC nonce mismatch")
        if ((isinstance(claims["aud"], list) and len(claims["aud"]) > 1) or "azp" in claims) and claims.get("azp") != cfg["client_id"]:
            fail(401, "OIDC authorized party mismatch")
        email = str(claims.get("email", "")).strip().lower()
        if not email or claims.get("email_verified") is not True:
            fail(401, "Verified OIDC email required")
    except HTTPException:
        raise
    except Exception:
        fail(401, "OIDC identity validation failed")
    tenant_id = cfg["tenant_id"]
    with request.app.state.database.tx(tenant_id) as conn:
        principal = db.row(conn, db.scoped(db.principals, tenant_id).where(db.principals.c.username == email,
            db.principals.c.kind.in_(["human", "scim"]), db.principals.c.active == True))
        if not principal:
            fail(403, "User must be provisioned by an administrator or SCIM before sign-in")
        # Binding prevents a subsequently recycled email from silently becoming the same identity.
        subject_key = "oidc:" + db.digest([cfg["issuer"], claims["sub"]])
        binding_name = "oidc_subject:" + principal["id"]
        bound = db.row(conn, db.scoped(db.cache, tenant_id).where(db.cache.c.project_id == "identity", db.cache.c.key == binding_name))
        if bound and bound["data"]["subject"] != subject_key:
            fail(403, "OIDC subject differs from the provisioned identity binding")
        if not bound:
            conn.execute(db.cache.insert().values(id=db.uid(), tenant_id=tenant_id, project_id="identity", key=binding_name,
                data={"subject": subject_key}, expires_at=253402300799., created_at=time.time()))
        token, _ = issue_token(conn, principal, "browser-session", 1/3)
        db.audit_event(conn, tenant_id, principal["id"], "sso.login", details={"issuer": cfg["issuer"]})
    response = cookie(RedirectResponse("/", status_code=303), token, settings)
    response.delete_cookie("ordeal_oidc_state", path="/auth/oidc")
    return response
