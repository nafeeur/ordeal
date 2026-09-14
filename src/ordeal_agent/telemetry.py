"""Explicit, lightweight production tracing and platform upload; no global monkey-patching."""
from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
import inspect
import json
import threading
import time
import uuid
from urllib.parse import urlsplit, quote
import math
from typing import Any

import httpx

from .privacy import redact

_PARENT: ContextVar[str | None] = ContextVar("ordeal_span_parent", default=None)


class PlatformClient:
    def __init__(self, base_url: str, token: str, project_id: str, *, timeout: float = 20):
        parsed = urlsplit(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("base_url must be an HTTP(S) origin without credentials, paths, or query")
        if not token or not project_id or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("A token, project_id and positive timeout are required")
        self.base_url = base_url.rstrip("/")
        self.project_id = quote(project_id, safe="")
        self.http = httpx.Client(base_url=self.base_url, headers={"Authorization": f"Bearer {token}"}, timeout=timeout, follow_redirects=False, trust_env=False)

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def request(self, method: str, path: str, data=None):
        if not path.startswith("/") or path.startswith("//") or any(c in path for c in "\r\n\\"):
            raise ValueError("Use a relative API path; credentials must remain on the configured origin")
        response = self.http.request(method, path, json=data)
        response.raise_for_status()
        return response.json() if response.content else None

    def ingest(self, trace: dict):
        # A fixed trace_id makes retrying the exact envelope idempotent at the server.
        envelope = dict(trace)
        envelope.setdefault("trace_id", uuid.uuid4().hex)
        return self.request("POST", f"/api/v1/projects/{self.project_id}/traces", envelope)

    def create(self, kind: str, name: str, data: dict):
        return self.request("POST", f"/api/v1/projects/{self.project_id}/resources", {"kind": kind, "name": name, "data": data})

    def replay_dataset(self, resource_id: str, *, version: int, assertions, concurrency=1, strict_order=True):
        from .datasets import replay_suite
        if type(version) is not int or version < 1:
            raise ValueError("Pin a positive dataset version")
        resource = self.request("GET", f"/api/v1/resources/{quote(resource_id, safe='')}?version={version}")
        if resource["kind"] != "dataset":
            raise ValueError("Resource is not a dataset")
        return replay_suite(resource["name"], resource["data"], assertions=assertions,
                            concurrency=concurrency, strict_order=strict_order)

    def enqueue(self, suite_path: str, *, labels=None, estimated_cost_usd=0, idempotency_key=None):
        return self.request("POST", f"/api/v1/projects/{self.project_id}/jobs", {"kind": "suite", "payload": {"suite_path": suite_path},
            "labels": labels or [], "estimated_cost_usd": estimated_cost_usd, "idempotency_key": idempotency_key or uuid.uuid4().hex})

    def trace(self, name: str, *, environment="production", agent_version=None, fail_open=True, redact_pii=True,
              suppress_inputs=False, suppress_outputs=False):
        return TraceSession(self, name, environment=environment, agent_version=agent_version, fail_open=fail_open,
                            redact_pii=redact_pii, suppress_inputs=suppress_inputs, suppress_outputs=suppress_outputs)

    def upload_report(self, report, *, name=None):
        data = report.to_dict() if hasattr(report, "to_dict") else report
        return self.create("experiment", name or f"{data.get('suite', 'suite')}-{uuid.uuid4().hex[:8]}", {"report": data})


@dataclass
class Span:
    session: "TraceSession"
    name: str
    kind: str = "tool"
    arguments: Any = None
    usage: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    output: Any = None
    _has_output: bool = False

    def set_output(self, value):
        self.output = deepcopy(value)
        self._has_output = True
        return value

    def __enter__(self):
        self.start = time.time_ns()
        self.parent = _PARENT.get()
        self.context_token = _PARENT.set(self.id)
        if self.kind == "tool":
            self.session.event("tool_call", self.name, self.start, arguments=self.arguments, call_id=self.id)
        elif self.kind == "model":
            self.session.event("model_call", self.name, self.start, input=self.arguments, call_id=self.id)
        elif self.kind == "handoff":
            self.session.event("handoff", self.name, self.start, arguments=self.arguments, call_id=self.id)
        return self

    def __exit__(self, exc_type, exc, tb):
        end = time.time_ns()
        _PARENT.reset(self.context_token)
        if exc:
            self.session.event("error", self.name, end, type=exc_type.__name__, message=str(exc), call_id=self.id)
            self.session.status = "error"
        elif self._has_output:
            self.session.event("tool_result" if self.kind == "tool" else "model_result", self.name, end, result=self.output, call_id=self.id)
        with self.session.lock:
            self.session.spans.append({"span_id": self.id, "parent_span_id": self.parent, "name": self.name,
                "kind": self.kind, "start_ns": self.start, "end_ns": end,
                "input": self.arguments, "output": self.output if self._has_output else None,
                "status": "error" if exc else "ok", "usage": dict(self.usage)})
            for k in ("input_tokens", "output_tokens", "total_tokens", "cost_usd"):
                if k in self.usage:
                    self.session.usage[k] = self.session.usage.get(k, 0) + self.usage[k]
        return False


class TraceSession:
    def __init__(self, client: PlatformClient, name: str, *, environment="production", agent_version=None,
                 fail_open=True, redact_pii=True, suppress_inputs=False, suppress_outputs=False):
        self.client, self.name = client, name
        self.environment, self.agent_version = environment, agent_version
        self.fail_open, self.redact_pii = fail_open, redact_pii
        self.suppress_inputs, self.suppress_outputs = suppress_inputs, suppress_outputs
        self.id = uuid.uuid4().hex
        self.events, self.spans = [], []
        self.usage, self.metadata = {}, {}
        self.initial_state, self.final_state = {}, {}
        self.output, self.last_error, self.response = None, None, None
        self.status = "unset"
        self.lock = threading.Lock()
        self.sent = False

    def __enter__(self):
        self.start = time.perf_counter()
        self.parent_token = _PARENT.set(None)
        return self

    def __exit__(self, exc_type, exc, tb):
        _PARENT.reset(self.parent_token)
        if exc:
            self.status = "error"
            self.event("error", "agent", time.time_ns(), type=exc_type.__name__, message=str(exc))
        self.duration = (time.perf_counter() - self.start) * 1000
        try:
            self.flush()
        except Exception as export_error:
            self.last_error = export_error
            if not self.fail_open and exc is None:
                raise
        return False

    def event(self, kind, name, timestamp=None, **payload):
        with self.lock:
            self.events.append({"seq": len(self.events), "kind": kind, "name": name,
                                "time_ns": timestamp or time.time_ns(), "payload": deepcopy(payload)})

    def span(self, name, *, kind="tool", arguments=None, usage=None):
        return Span(self, name, kind, deepcopy(arguments), deepcopy(usage or {}))

    async def call(self, name, fn, **arguments):
        with self.span(name, arguments=arguments) as span:
            value = fn(**arguments)
            if inspect.isawaitable(value):
                value = await value
            return span.set_output(value)

    def set_output(self, value):
        self.output = deepcopy(value)
        return value

    def flush(self):
        if self.sent:
            return self.response
        envelope = {"trace_id": self.id, "name": self.name, "environment": self.environment,
            "agent_version": self.agent_version, "status": self.status,
            "duration_ms": getattr(self, "duration", (time.perf_counter() - self.start) * 1000),
            "usage": self.usage, "spans": self.spans,
            "trajectory": {"events": self.events, "initial_state": self.initial_state,
                           "final_state": self.final_state, "final_output": self.output},
            "metadata": {**self.metadata, "source": "ordeal-python", "completeness": "instrumented_scope_only"}}
        clean = redact(envelope, pii=self.redact_pii, suppress_inputs=self.suppress_inputs, suppress_outputs=self.suppress_outputs)
        self.response = self.client.ingest(clean)
        self.sent = True
        return self.response
