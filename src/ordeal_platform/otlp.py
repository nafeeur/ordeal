"""OTLP trace ingestion: HTTP JSON/protobuf and gRPC, plus OpenInference/GenAI normalization.

OTLP has no end-of-trace marker. Stored snapshots are explicitly not evidence of a complete execution.
"""
from __future__ import annotations

import json
import math
import base64
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import sqlalchemy as sa
from fastapi import HTTPException

from . import db, schemas, services
from .security import authenticate, fail


HEX_TRACE = re.compile(r"^[0-9a-fA-F]{32}$")
HEX_SPAN = re.compile(r"^[0-9a-fA-F]{16}$")


def object_value(value):
    if not isinstance(value, dict):
        fail(422, "OTLP expected an object")
    return value


def array_value(value):
    if not isinstance(value, list):
        fail(422, "OTLP expected an array")
    return value


def any_value(value, depth=0):
    object_value(value)
    if depth > 40:
        fail(422, "OTLP attribute nesting exceeds 40 levels")
    known = set(value) & {"stringValue", "boolValue", "intValue", "doubleValue", "arrayValue", "kvlistValue", "bytesValue"}
    if len(known) > 1:
        fail(422, "OTLP AnyValue requires a single value type")
    if not known:
        return None
    kind = next(iter(known)); data = value[kind]
    if kind in {"stringValue", "bytesValue"}:
        if not isinstance(data, str):
            fail(422, "Invalid OTLP string value")
        if kind == "bytesValue":
            try: base64.b64decode(data, validate=True)
            except ValueError: fail(422, "Invalid OTLP base64 value")
        return data
    if kind == "boolValue":
        if type(data) is not bool: fail(422, "Invalid OTLP boolean value")
        return data
    if kind == "intValue":
        if type(data) not in {int, str} or (isinstance(data, str) and not re.fullmatch(r"-?[0-9]+", data)):
            fail(422, "Invalid OTLP integer value")
        result = int(data)
        if not -(2**63) <= result < 2**63: fail(422, "OTLP integer out of range")
        return result
    if kind == "doubleValue":
        if type(data) not in {float, int} or not math.isfinite(data): fail(422, "Finite OTLP double required")
        return float(data)
    nested = object_value(data).get("values", [])
    return ([any_value(v, depth + 1) for v in array_value(nested)] if kind == "arrayValue"
            else attributes(nested, depth + 1))


def attributes(values, depth=0):
    result = {}
    for item in array_value(values):
        object_value(item)
        if not isinstance(item.get("key"), str): fail(422, "OTLP attribute key must be a string")
        if item["key"] in result: fail(422, "Duplicate OTLP attribute key")
        result[item["key"]] = any_value(item.get("value", {}), depth)
    return result


def encode_value(value):
    if isinstance(value, bool):
        return {"boolValue": value}
    if isinstance(value, int):
        return {"intValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [encode_value(v) for v in value]}}
    if isinstance(value, dict):
        return {"kvlistValue": {"values": [{"key": k, "value": encode_value(v)} for k, v in value.items()]}}
    return {"stringValue": str(value) if value is not None else ""}


def decode_protobuf(body: bytes) -> dict:
    from google.protobuf.json_format import MessageToDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    message = ExportTraceServiceRequest()
    message.ParseFromString(body)
    result = MessageToDict(message)
    # OTLP JSON IDs are hex, unlike generic protobuf's base64 encoding for bytes fields.
    for rs, raw_rs in zip(result.get("resourceSpans", []), message.resource_spans):
        for ss, raw_ss in zip(rs.get("scopeSpans", []), raw_rs.scope_spans):
            for span, raw_span in zip(ss.get("spans", []), raw_ss.spans):
                span["traceId"] = raw_span.trace_id.hex()
                span["spanId"] = raw_span.span_id.hex()
                span["parentSpanId"] = raw_span.parent_span_id.hex()
                for link, raw_link in zip(span.get("links", []), raw_span.links):
                    link["traceId"], link["spanId"] = raw_link.trace_id.hex(), raw_link.span_id.hex()
    return result


def normalize(document: dict, maximum=10000) -> dict[str, list[dict]]:
    object_value(document)
    grouped = defaultdict(list)
    count = 0
    for resource in array_value(document.get("resourceSpans", [])):
        object_value(resource)
        resource_attrs = attributes(object_value(resource.get("resource", {})).get("attributes", []))
        for scope in array_value(resource.get("scopeSpans", [])):
            object_value(scope)
            for span in array_value(scope.get("spans", [])):
                object_value(span)
                count += 1
                if count > maximum:
                    fail(413, "Too many spans in batch")
                tid, sid = span.get("traceId", ""), span.get("spanId", "")
                parent = span.get("parentSpanId", "")
                if not all(isinstance(x, str) for x in (tid, sid, parent)):
                    fail(422, "OTLP IDs must be hex strings")
                if not HEX_TRACE.fullmatch(tid) or int(tid, 16) == 0 or not HEX_SPAN.fullmatch(sid) or int(sid, 16) == 0:
                    fail(422, "OTLP requires nonzero hex traceId/spanId values")
                if parent and (not HEX_SPAN.fullmatch(parent) or int(parent, 16) == 0):
                    fail(422, "Invalid parentSpanId")
                if parent.lower() == sid.lower():
                    fail(422, "Span cannot parent itself")
                raw_times = (span.get("startTimeUnixNano", 0), span.get("endTimeUnixNano", 0))
                if any(type(t) not in {int, str} or (isinstance(t, str) and not t.isdecimal()) for t in raw_times):
                    fail(422, "Invalid OTLP timestamps")
                start, end = map(int, raw_times)
                if start < 0 or end < start or end >= 2 ** 64:
                    fail(422, "Invalid span timestamps")
                a = attributes(span.get("attributes", []))
                grouped[tid.lower()].append({
                    "trace_id": tid.lower(), "span_id": sid.lower(), "parent_span_id": parent.lower(),
                    "name": str(span.get("name", "span"))[:200], "start_ns": start, "end_ns": end,
                    "kind": span.get("kind", 0), "attributes": a, "resource": resource_attrs,
                    "scope": object_value(scope.get("scope", {})), "status": object_value(span.get("status", {})),
                    "events": array_value(span.get("events", [])), "links": array_value(span.get("links", [])),
                })
    return dict(grouped)


def parsed(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def snapshot(trace_id: str, spans: list[dict]) -> schemas.TraceIn:
    parents = {s["span_id"]: s["parent_span_id"] for s in spans}
    for sid in parents:
        visited = set()
        cursor = sid
        while cursor and cursor in parents:
            if cursor in visited:
                fail(422, "Span parent cycle detected")
            visited.add(cursor)
            cursor = parents[cursor]
    ordered = sorted(spans, key=lambda s: (s["start_ns"], s["span_id"]))
    root = next((s for s in ordered if not s["parent_span_id"]), ordered[0])
    events = []
    usage = {}
    failed = False
    for s in ordered:
        a = s["attributes"]
        role = str(a.get("openinference.span.kind", a.get("gen_ai.operation.name", ""))).upper()
        name = str(a.get("tool.name", a.get("gen_ai.tool.name", s["name"])))
        error = s["status"].get("code") in (2, "STATUS_CODE_ERROR")
        failed |= error
        if role in {"TOOL", "EXECUTE_TOOL"} or "tool.name" in a or "gen_ai.tool.name" in a:
            args = parsed(a.get("tool.parameters", a.get("input.value", a.get("gen_ai.tool.call.arguments"))))
            events.append({"kind": "tool_call", "name": name, "payload": {"arguments": args, "call_id": s["span_id"]}, "time_ns": s["start_ns"]})
            output = a.get("output.value", a.get("gen_ai.tool.call.result"))
            if output is not None:
                events.append({"kind": "tool_result", "name": name, "payload": {"result": parsed(output), "call_id": s["span_id"]}, "time_ns": s["end_ns"]})
        if role in {"LLM", "CHAT", "TEXT_COMPLETION", "GENERATE_CONTENT"}:
            events.append({"kind": "model_call", "name": str(a.get("gen_ai.request.model", a.get("llm.model_name", s["name"]))), "payload": {"span_id": s["span_id"]}, "time_ns": s["start_ns"]})
            mapping = {"input_tokens": ["gen_ai.usage.input_tokens", "llm.token_count.prompt"],
                       "output_tokens": ["gen_ai.usage.output_tokens", "llm.token_count.completion"],
                       "cost_usd": ["llm.cost.total", "gen_ai.usage.cost"]}
            for dest, alternatives in mapping.items():
                val = next((a[k] for k in alternatives if k in a), None)
                if val is not None:
                    usage[dest] = usage.get(dest, 0) + float(val)
        if error:
            events.append({"kind": "error", "name": s["name"], "payload": {"span_id": s["span_id"], "message": s["status"].get("message", "")}, "time_ns": s["end_ns"]})
    if "input_tokens" in usage and "output_tokens" in usage:
        usage["total_tokens"] = int(usage["input_tokens"] + usage["output_tokens"])
    events.sort(key=lambda e: (e["time_ns"], 0 if e["kind"].endswith("_call") else 1))
    for i, event in enumerate(events):
        event["seq"] = i
    return schemas.TraceIn(trace_id=trace_id, name=root["name"], status="error" if failed else "unset",
        environment=str(root["resource"].get("deployment.environment.name", root["resource"].get("deployment.environment", "production"))),
        duration_ms=(max(s["end_ns"] for s in spans) - min(s["start_ns"] for s in spans)) / 1e6,
        usage=usage, trajectory={"events": events, "final_output": parsed(root["attributes"].get("output.value"))},
        spans=ordered, metadata={"source": "otlp", "completeness": "unknown", "missing_parent_count": sum(bool(s["parent_span_id"]) and s["parent_span_id"] not in parents for s in spans)})


def ingest(conn, ident, project_id, document, settings):
    proj = services.project(conn, ident, project_id, "write")
    services.lock_project(conn, project_id)
    grouped = normalize(document, settings.max_trace_events)
    written = 0
    for tid, batch in grouped.items():
        batch = services.redact_for_project(batch, proj)
        existing = db.row(conn, db.scoped(db.traces, ident.tenant_id).where(db.traces.c.project_id == project_id, db.traces.c.trace_id == tid))
        if existing:
            if existing["data"].get("metadata", {}).get("source") != "otlp":
                fail(409, "Trace id belongs to a non-OTLP trace")
            merged = {s["span_id"]: s for s in existing["data"].get("spans", [])}
            modified = False
            for span in batch:
                old = merged.get(span["span_id"])
                if old and old != span:
                    fail(409, "Conflicting duplicate span id")
                modified |= old is None
                merged[span["span_id"]] = span
            if len(merged) > settings.max_trace_events:
                fail(413, "Trace exceeds maximum span count")
            if modified:
                data = snapshot(tid, list(merged.values())).model_dump()
                data = services.redact_for_project(data, proj)
                delta = len(db.canonical(data).encode()) - len(db.canonical(existing["data"]).encode())
                services.meter(conn, ident, project_id, "trace_bytes", max(delta, 0), limit=proj["settings"].get("limits", {}).get("bytes_daily"))
                conn.execute(db.traces.update().where(db.traces.c.id == existing["id"]).values(data=data, content_hash=db.digest(data),
                    duration_ms=data["duration_ms"], status=data["status"], cost_usd=data["usage"].get("cost_usd"), total_tokens=data["usage"].get("total_tokens")))
                db.audit_event(conn, ident.tenant_id, ident.id, "trace.spans_appended", existing["id"], {"span_count": len(merged), "hash": db.digest(data)})
        else:
            services.ingest_trace(conn, ident, project_id, snapshot(tid, batch), settings)
        written += len(batch)
    return written


def export_document(record: dict) -> dict:
    data = record.get("data", record)
    resource_spans = []
    for s in data.get("spans", []):
        span = {"traceId": s["trace_id"], "spanId": s["span_id"], "parentSpanId": s["parent_span_id"],
                "name": s["name"], "kind": s["kind"], "startTimeUnixNano": str(s["start_ns"]), "endTimeUnixNano": str(s["end_ns"]),
                "attributes": [{"key": k, "value": encode_value(v)} for k, v in s["attributes"].items()],
                "status": s["status"], "events": s.get("events", []), "links": s.get("links", [])}
        resource_spans.append({"resource": {"attributes": [{"key": k, "value": encode_value(v)} for k, v in s["resource"].items()]},
                               "scopeSpans": [{"scope": s.get("scope", {}), "spans": [span]}]})
    return {"resourceSpans": resource_spans}


def grpc_server(database, settings, address="127.0.0.1:4317", *, server_credentials=None):
    import grpc
    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2, trace_service_pb2_grpc

    class TraceService(trace_service_pb2_grpc.TraceServiceServicer):
        def Export(self, request, context):
            meta = dict(context.invocation_metadata())
            token = meta.get("authorization", "")
            try:
                ident = authenticate(database, token[7:] if token.lower().startswith("bearer ") else "")
                project_id = meta.get("x-ordeal-project", "")
                document = decode_protobuf(request.SerializeToString())
                with database.tx(ident.tenant_id) as conn:
                    ingest(conn, ident, project_id, document, settings)
                return trace_service_pb2.ExportTraceServiceResponse()
            except HTTPException as exc:
                mapping = {401: grpc.StatusCode.UNAUTHENTICATED, 403: grpc.StatusCode.PERMISSION_DENIED,
                           404: grpc.StatusCode.NOT_FOUND, 429: grpc.StatusCode.RESOURCE_EXHAUSTED}
                context.abort(mapping.get(exc.status_code, grpc.StatusCode.INVALID_ARGUMENT), str(exc.detail))
            except Exception:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid trace batch")

    server = grpc.server(ThreadPoolExecutor(max_workers=8), options=[("grpc.max_receive_message_length", settings.max_body_bytes)])
    trace_service_pb2_grpc.add_TraceServiceServicer_to_server(TraceService(), server)
    if server_credentials:
        port = server.add_secure_port(address, server_credentials)
    elif not settings.production:
        port = server.add_insecure_port(address)
    else:
        raise ValueError("Production gRPC requires TLS server_credentials")
    if port == 0:
        raise RuntimeError("Could not bind gRPC listener")
    server.start()
    return server, port
