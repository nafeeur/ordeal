from __future__ import annotations

import json
import logging
import time
import uuid
import zlib
from collections import OrderedDict

from starlette.responses import JSONResponse

logger = logging.getLogger("ordeal.http")


class SecurityMiddleware:
    """Bound request bodies before parsing and apply browser security headers.

    Per-process IP throttling is a secondary safeguard. Distributed quotas are enforced in the database.
    A production edge proxy must also rate-limit and cap bodies.
    """
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.buckets = OrderedDict()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        request_id = uuid.uuid4().hex
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        method = scope["method"]
        path = scope["path"]
        status = 500

        async def secured_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                extra = [
                    (b"x-request-id", request_id.encode()),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
                ]
                if self.settings.production:
                    extra.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                if path.startswith(("/api/", "/auth/", "/scim/")):
                    extra.append((b"cache-control", b"no-store"))
                message["headers"] = list(message.get("headers", [])) + extra
            await send(message)

        async def error(code, detail):
            await JSONResponse({"detail": detail, "request_id": request_id}, status_code=code)(scope, receive, secured_send)

        if headers.get("cookie") and "ordeal_session=" in headers["cookie"] and not headers.get("authorization") and method not in {"GET", "HEAD", "OPTIONS"}:
            if headers.get("origin") != self.settings.public_url:
                return await error(403, "Same-origin request required for cookie-authenticated writes")
        # Do not trust X-Forwarded-For without an explicitly configured edge proxy.
        ip = (scope.get("client") or ("unknown",))[0]
        slot = int(time.time() // 60)
        key = (ip, slot)
        self.buckets[key] = self.buckets.get(key, 0) + 1
        self.buckets.move_to_end(key)
        while len(self.buckets) > 10000:
            self.buckets.popitem(last=False)
        if self.buckets[key] > self.settings.request_limit and path not in {"/healthz", "/readyz"}:
            return await error(429, "Request rate limit exceeded")
        try:
            announced = int(headers.get("content-length", "0"))
            if announced < 0 or announced > self.settings.max_body_bytes:
                return await error(413, "Request body too large")
        except ValueError:
            return await error(400, "Invalid content length")
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            body.extend(message.get("body", b""))
            if len(body) > self.settings.max_body_bytes:
                return await error(413, "Request body too large")
            if not message.get("more_body", False):
                break
        if headers.get("content-encoding"):
            if headers["content-encoding"].lower() != "gzip":
                return await error(415, "Unsupported content encoding")
            try:
                inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
                decoded = inflater.decompress(body, self.settings.max_body_bytes + 1)
                if len(decoded) > self.settings.max_body_bytes or inflater.unconsumed_tail:
                    return await error(413, "Decompressed request too large")
                if not inflater.eof or inflater.unused_data:
                    return await error(400, "Invalid or concatenated gzip body")
                body = decoded
                scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() not in {b"content-encoding", b"content-length"}] + [(b"content-length", str(len(body)).encode())]
            except zlib.error:
                return await error(400, "Invalid gzip body")
        consumed = False

        async def buffered_receive():
            nonlocal consumed
            if consumed:
                return await receive()
            consumed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        scope.setdefault("state", {})["request_id"] = request_id
        try:
            await self.app(scope, buffered_receive, secured_send)
        finally:
            logger.info(json.dumps({"request_id": request_id, "method": method, "path": path,
                "status": status, "duration_ms": round((time.perf_counter() - started) * 1000, 2)}))
