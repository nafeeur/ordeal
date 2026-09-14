"""SCIM 2.0 Users and Groups provisioning subset with deactivation and group-to-role mapping."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from . import db
from .security import Identity, identity, fail

router = APIRouter(prefix="/scim/v2")
USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
GROUP_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"


async def object_body(request):
    try:
        body = await request.json()
    except (ValueError, RecursionError):
        fail(400, "Expected a JSON object")
    if not isinstance(body, dict):
        fail(400, "Expected a JSON object")
    for key in ("userName", "displayName", "externalId"):
        if key in body and body[key] is not None and (not isinstance(body[key], str) or len(body[key]) > 320):
            fail(400, "Invalid SCIM string attribute")
    return body


def operations(body):
    items = body.get("Operations")
    if not isinstance(items, list) or not items or len(items) > 100:
        fail(400, "PATCH requires one to 100 Operations")
    for op in items:
        if not isinstance(op, dict) or not isinstance(op.get("op"), str) or not isinstance(op.get("path", ""), str):
            fail(400, "Invalid PATCH operation")
    return items


def member_ids(items):
    if not isinstance(items, list) or len(items) > 10000:
        fail(400, "members must be a bounded array")
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("value"), str) or not item["value"]:
            fail(400, "Each member requires a string value")
    return {item["value"] for item in items}


def response(data, status=200):
    return JSONResponse(data, status_code=status, media_type="application/scim+json")


def scim_identity(request: Request):
    return identity(request).require_org("scim")


def as_user(item, request):
    return {"schemas": [USER_SCHEMA], "id": item["id"], "externalId": item["external_id"],
        "userName": item["username"], "displayName": item["name"], "active": item["active"],
        "meta": {"resourceType": "User", "created": datetime.fromtimestamp(item["created_at"], timezone.utc).isoformat(),
                 "location": request.app.state.settings.public_url + "/scim/v2/Users/" + item["id"]}}


def as_group(item, request):
    return {"schemas": [GROUP_SCHEMA], "id": item["id"], "displayName": item["name"],
        "members": [{"value": x} for x in item["members"]], "meta": {"resourceType": "Group",
        "location": request.app.state.settings.public_url + "/scim/v2/Groups/" + item["id"]}}


def find_user(conn, ident, user_id):
    user = db.row(conn, db.scoped(db.principals, ident.tenant_id).where(db.principals.c.id == user_id, db.principals.c.kind == "scim"))
    if not user:
        fail(404, "SCIM user not found")
    return user


def recompute_roles(conn, ident, members):
    groups = db.rows(conn, db.scoped(db.groups, ident.tenant_id))
    ranks = ["viewer", "reviewer", "billing", "developer", "admin"]
    for member in members:
        user = find_user(conn, ident, member)
        mapped = [g["role"] for g in groups if member in g["members"] and g["role"] in ranks]
        role = max(mapped or ["viewer"], key=ranks.index)
        conn.execute(db.principals.update().where(db.principals.c.id == user["id"]).values(role=role))


@router.get("/ServiceProviderConfig")
def config(ident: Identity = Depends(scim_identity)):
    return response({"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "patch": {"supported": True}, "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": 200}, "changePassword": {"supported": False},
        "sort": {"supported": False}, "etag": {"supported": False},
        "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Bearer token", "description": "Scoped SCIM service account token"}]})


@router.get("/Users")
def list_users(request: Request, startIndex: int = 1, count: int = 100, filter: str | None = None, ident: Identity = Depends(scim_identity)):
    with request.app.state.database.tx(ident.tenant_id) as conn:
        query = db.scoped(db.principals, ident.tenant_id).where(db.principals.c.kind == "scim")
        if filter:
            match = re.fullmatch(r'(userName|externalId|id)\s+eq\s+"([^"\\]*)"', filter)
            if not match:
                fail(400, "Supported filters: userName, externalId or id eq a quoted literal")
            column = {"userName": db.principals.c.username, "externalId": db.principals.c.external_id, "id": db.principals.c.id}[match[1]]
            value = match[2].lower() if match[1] == "userName" else match[2]
            query = query.where(column == value)
        total = conn.execute(sa.select(sa.func.count()).select_from(query.subquery())).scalar()
        items = db.rows(conn, query.order_by(db.principals.c.id).offset(max(startIndex - 1, 0)).limit(max(0, min(count, 200))))
    return response({"schemas": [LIST_SCHEMA], "totalResults": total, "startIndex": max(1, startIndex),
                     "itemsPerPage": len(items), "Resources": [as_user(x, request) for x in items]})


@router.post("/Users")
async def create_user(request: Request, ident: Identity = Depends(scim_identity)):
    body = await object_body(request)
    username = str(body.get("userName", "")).strip().lower()
    if not username or len(username) > 320 or type(body.get("active", True)) is not bool:
        fail(400, "Valid userName and boolean active required")
    with request.app.state.database.tx(ident.tenant_id) as conn:
        existing = db.row(conn, db.scoped(db.principals, ident.tenant_id).where(db.principals.c.username == username))
        if existing:
            fail(409, "userName already exists")
        item = dict(id=db.uid(), tenant_id=ident.tenant_id, name=str(body.get("displayName", username))[:200], username=username,
            external_id=body.get("externalId"), role="viewer", kind="scim", projects=[], permissions=[],
            active=body.get("active", True), created_at=time.time())
        conn.execute(db.principals.insert().values(**item))
        db.audit_event(conn, ident.tenant_id, ident.id, "scim.user.created", item["id"])
    return response(as_user(item, request), 201)


@router.get("/Users/{user_id}")
def get_user(user_id: str, request: Request, ident: Identity = Depends(scim_identity)):
    with request.app.state.database.tx(ident.tenant_id) as conn:
        return response(as_user(find_user(conn, ident, user_id), request))


@router.patch("/Users/{user_id}")
async def patch_user(user_id: str, request: Request, ident: Identity = Depends(scim_identity)):
    body = await object_body(request)
    changes = {}
    for operation in operations(body):
        if operation.get("op", "").lower() not in {"replace", "add"}:
            fail(400, "User PATCH supports add/replace of active, displayName and userName")
        path = operation.get("path")
        values = {path: operation.get("value")} if path else operation.get("value", {})
        if not isinstance(values, dict):
            fail(400, "Invalid PATCH value")
        for key, value in values.items():
            mapping = {"active": "active", "displayName": "name", "userName": "username"}
            if key not in mapping:
                fail(400, "Unsupported user PATCH attribute")
            if key == "active" and type(value) is not bool:
                fail(400, "active must be boolean")
            if key != "active" and (not isinstance(value, str) or not value.strip() or len(value) > 320):
                fail(400, "Invalid user attribute")
            changes[mapping[key]] = value.strip().lower() if key == "userName" else value
    with request.app.state.database.tx(ident.tenant_id) as conn:
        user = find_user(conn, ident, user_id)
        conn.execute(db.principals.update().where(db.principals.c.id == user_id).values(**changes))
        if changes.get("active") is False:
            conn.execute(db.tokens.update().where(db.tokens.c.principal_id == user_id, db.tokens.c.tenant_id == ident.tenant_id).values(revoked=True))
        db.audit_event(conn, ident.tenant_id, ident.id, "scim.user.updated", user_id, {"fields": list(changes)})
        user.update(changes)
    return response(as_user(user, request))


@router.put("/Users/{user_id}")
async def replace_user(user_id: str, request: Request, ident: Identity = Depends(scim_identity)):
    body = await object_body(request)
    username = str(body.get("userName", "")).strip().lower()
    if not username or len(username) > 320 or type(body.get("active", True)) is not bool:
        fail(400, "Invalid userName or active")
    with request.app.state.database.tx(ident.tenant_id) as conn:
        user = find_user(conn, ident, user_id)
        changes = {"username": username, "name": str(body.get("displayName", username))[:200],
                   "external_id": body.get("externalId"), "active": body.get("active", True)}
        conn.execute(db.principals.update().where(db.principals.c.id == user_id).values(**changes))
        if not changes["active"]:
            conn.execute(db.tokens.update().where(db.tokens.c.principal_id == user_id).values(revoked=True))
        db.audit_event(conn, ident.tenant_id, ident.id, "scim.user.replaced", user_id)
        user.update(changes)
    return response(as_user(user, request))


@router.delete("/Users/{user_id}")
def delete_user(user_id: str, request: Request, ident: Identity = Depends(scim_identity)):
    with request.app.state.database.tx(ident.tenant_id) as conn:
        find_user(conn, ident, user_id)
        conn.execute(db.principals.update().where(db.principals.c.id == user_id).values(active=False))
        conn.execute(db.tokens.update().where(db.tokens.c.principal_id == user_id).values(revoked=True))
        db.audit_event(conn, ident.tenant_id, ident.id, "scim.user.deprovisioned", user_id)
    return JSONResponse(None, status_code=204)


@router.get("/Groups")
def list_groups(request: Request, startIndex: int = 1, count: int = 100, filter: str | None = None, ident: Identity = Depends(scim_identity)):
    with request.app.state.database.tx(ident.tenant_id) as conn:
        query = db.scoped(db.groups, ident.tenant_id)
        if filter:
            match = re.fullmatch(r'displayName\s+eq\s+"([^"\\]*)"', filter)
            if not match:
                fail(400, "Only displayName eq filter supported")
            query = query.where(db.groups.c.name == match[1])
        total = conn.execute(sa.select(sa.func.count()).select_from(query.subquery())).scalar()
        items = db.rows(conn, query.order_by(db.groups.c.id).offset(max(0, startIndex - 1)).limit(max(0, min(count, 200))))
    return response({"schemas": [LIST_SCHEMA], "totalResults": total, "startIndex": max(1, startIndex), "itemsPerPage": len(items), "Resources": [as_group(x, request) for x in items]})


@router.post("/Groups")
async def create_group(request: Request, ident: Identity = Depends(scim_identity)):
    body = await object_body(request)
    name = str(body.get("displayName", "")).strip()
    if not name or len(name) > 200:
        fail(400, "displayName required")
    members = sorted(member_ids(body.get("members", [])))
    role = request.app.state.settings.oidc.get("group_roles", {}).get(name, "viewer")
    if role not in {"viewer", "reviewer", "billing", "developer", "admin"}:
        fail(400, "Invalid operator group-role mapping")
    with request.app.state.database.tx(ident.tenant_id) as conn:
        for member in members:
            find_user(conn, ident, member)
        item = dict(id=db.uid(), tenant_id=ident.tenant_id, name=name, members=members, role=role, created_at=time.time())
        conn.execute(db.groups.insert().values(**item))
        recompute_roles(conn, ident, members)
        db.audit_event(conn, ident.tenant_id, ident.id, "scim.group.created", item["id"])
    return response(as_group(item, request), 201)


@router.get("/Groups/{group_id}")
def get_group(group_id: str, request: Request, ident: Identity = Depends(scim_identity)):
    with request.app.state.database.tx(ident.tenant_id) as conn:
        group = db.row(conn, db.scoped(db.groups, ident.tenant_id).where(db.groups.c.id == group_id))
        if not group:
            fail(404, "Group not found")
        return response(as_group(group, request))


@router.patch("/Groups/{group_id}")
async def patch_group(group_id: str, request: Request, ident: Identity = Depends(scim_identity)):
    body = await object_body(request)
    with request.app.state.database.tx(ident.tenant_id) as conn:
        group = db.row(conn, db.scoped(db.groups, ident.tenant_id).where(db.groups.c.id == group_id))
        if not group:
            fail(404, "Group not found")
        old_members = list(group["members"])
        members = set(old_members)
        for operation in operations(body):
            op, path = operation.get("op", "").lower(), operation.get("path", "")
            value = operation.get("value", [])
            if not path and isinstance(value, dict) and "members" in value:
                path, value = "members", value["members"]
            match = re.fullmatch(r'members\[value\s+eq\s+"([^"\\]+)"\]', path)
            if op == "remove" and match:
                members.discard(match[1])
            elif path == "members" and op in {"add", "replace", "remove"}:
                ids = member_ids(value)
                if op == "replace":
                    members = ids
                elif op == "add":
                    members |= ids
                elif ids:
                    members -= ids
                else:
                    members.clear()
            else:
                fail(400, "Only member add/replace/remove group patches are supported")
        for member in members:
            find_user(conn, ident, member)
        group["members"] = sorted(members)
        conn.execute(db.groups.update().where(db.groups.c.id == group_id).values(members=group["members"]))
        recompute_roles(conn, ident, set(old_members) | members)
        db.audit_event(conn, ident.tenant_id, ident.id, "scim.group.updated", group_id)
    return response(as_group(group, request))


@router.delete("/Groups/{group_id}")
def delete_group(group_id: str, request: Request, ident: Identity = Depends(scim_identity)):
    with request.app.state.database.tx(ident.tenant_id) as conn:
        group = db.row(conn, db.scoped(db.groups, ident.tenant_id).where(db.groups.c.id == group_id))
        if not group:
            fail(404, "Group not found")
        conn.execute(db.groups.delete().where(db.groups.c.id == group_id))
        recompute_roles(conn, ident, group["members"])
        db.audit_event(conn, ident.tenant_id, ident.id, "scim.group.deleted", group_id)
    return JSONResponse(None, status_code=204)
