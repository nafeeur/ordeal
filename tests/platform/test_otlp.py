from __future__ import annotations
from copy import deepcopy
import json
import grpc
import pytest
from google.protobuf.json_format import ParseDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2_grpc import TraceServiceStub
from ordeal_platform.otlp import grpc_server


def document(trace_id='a' * 32, span_id='1' * 16, parent='', name='lookup', start=1000000000):
    return {'resourceSpans': [{'resource': {'attributes': [{'key': 'service.name', 'value': {'stringValue': 'support'}}]},
        'scopeSpans': [{'scope': {'name': 'openinference.test'}, 'spans': [{
            'traceId': trace_id, 'spanId': span_id, 'parentSpanId': parent, 'name': name,
            'kind': 1, 'startTimeUnixNano': str(start), 'endTimeUnixNano': str(start + 1000000),
            'attributes': [
                {'key': 'openinference.span.kind', 'value': {'stringValue': 'TOOL'}},
                {'key': 'tool.name', 'value': {'stringValue': name}},
                {'key': 'input.value', 'value': {'stringValue': '{"customer":"person@example.com","password":"sensitive"}'}},
                {'key': 'output.value', 'value': {'stringValue': '{"ok":true}'}},
            ], 'status': {'code': 1}
        }]}]}]}


def protobuf_doc():
    from google.protobuf.json_format import ParseDict
    import base64
    data = document()
    span = data['resourceSpans'][0]['scopeSpans'][0]['spans'][0]
    span['traceId'] = base64.b64encode(bytes.fromhex('a' * 32)).decode()
    span['spanId'] = base64.b64encode(bytes.fromhex('1' * 16)).decode()
    return ParseDict(data, ExportTraceServiceRequest())


def test_otlp_json_incremental_merge_and_export(platform):
    p = platform
    h = {**p.headers, 'x-ordeal-project': p.project}
    p.call('POST', '/v1/traces', headers=h, json=document())
    p.call('POST', '/v1/traces', headers=h, json=document())
    p.call('POST', '/v1/traces', headers=h, json=document(span_id='2' * 16, parent='1' * 16, name='refund', start=1002000000))
    items = p.call('GET', f'/api/v1/projects/{p.project}/traces')['items']
    assert len(items) == 1
    t = p.call('GET', f'/api/v1/traces/{items[0]["id"]}')
    assert len(t['data']['spans']) == 2
    assert t['data']['metadata']['completeness'] == 'unknown'
    assert 'person@example.com' not in json.dumps(t) and 'sensitive' not in json.dumps(t)
    exported = p.call('GET', f'/api/v1/traces/{t["id"]}/otlp')
    assert len(exported['resourceSpans']) == 2
    assert exported['resourceSpans'][0]['scopeSpans'][0]['spans'][0]['traceId'] == 'a' * 32
    modified = document(name='changed')
    p.call('POST', '/v1/traces', headers=h, json=modified, expected=409)


def test_otlp_protobuf_http_and_real_grpc(platform):
    p = platform
    wire = protobuf_doc()
    h = {**p.headers, 'x-ordeal-project': p.project, 'Content-Type': 'application/x-protobuf'}
    r = p.client.post('/v1/traces', headers=h, content=wire.SerializeToString())
    assert r.status_code == 200 and r.headers['content-type'] == 'application/x-protobuf', r.text
    server, port = grpc_server(p.database, p.settings, '127.0.0.1:0')
    try:
        with grpc.insecure_channel(f'127.0.0.1:{port}') as channel:
            stub = TraceServiceStub(channel)
            with pytest.raises(grpc.RpcError) as err:
                stub.Export(wire, timeout=5)
            assert err.value.code() == grpc.StatusCode.UNAUTHENTICATED
            stub.Export(wire, metadata=(('authorization', p.headers['Authorization']), ('x-ordeal-project', p.project)), timeout=5)
        assert p.call('GET', f'/api/v1/projects/{p.project}/traces')['total'] == 1
    finally:
        server.stop(0).wait()


@pytest.mark.parametrize('change', [
    {'traceId': '0' * 32}, {'traceId': 'bad'}, {'spanId': '0' * 16}, {'spanId': 'nothex'},
    {'parentSpanId': '1' * 16}, {'endTimeUnixNano': '-1'}, {'startTimeUnixNano': 'bad'},
])
def test_invalid_spans_rejected(platform, change):
    p = platform
    data = document()
    data['resourceSpans'][0]['scopeSpans'][0]['spans'][0].update(change)
    p.call('POST', '/v1/traces', headers={**p.headers, 'x-ordeal-project': p.project}, json=data, expected=422)


def test_parent_cycle_rejected(platform):
    p = platform
    data = document(parent='2' * 16)
    second = document(span_id='2' * 16, parent='1' * 16)
    data['resourceSpans'] += second['resourceSpans']
    p.call('POST', '/v1/traces', headers={**p.headers, 'x-ordeal-project': p.project}, json=data, expected=422)
