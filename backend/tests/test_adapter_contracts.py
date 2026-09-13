import asyncio

from fastapi.testclient import TestClient

from app.adapters import AdapterDescriptor, AdapterRegistry, registry
from app.core import ACTION_SCHEMA_VERSION, ActionEnvelope, normalize_action
from app.engine import SimulationEngine
from app.main import app
from app.runtime_verifier import verify_runtime_execution


def test_action_envelope_is_stable_and_preserves_adapter_evidence():
    raw = {"seq": 1, "type": "tool.call", "tool": "charge", "args": {"amount": 25}, "vendor_field": "kept"}
    action = normalize_action(raw, 0)
    assert action["schema_version"] == ACTION_SCHEMA_VERSION
    assert action["kind"] == "call"
    assert action["name"] == "charge"
    assert action["data"] == {"input": {"amount": 25}}
    assert action["vendor_field"] == "kept"
    assert ActionEnvelope(id="e1", seq=1, kind="call", name="charge").fingerprint == ActionEnvelope(id="e1", seq=1, kind="call", name="charge").fingerprint


def test_registry_discovers_adapters_and_fails_closed_on_missing_capability():
    assert any(item["name"] == "observed_trace" for item in registry.list("target"))
    report = registry.check("runtime", "local", ["execute", "hardware_enclave"])
    assert report["compatible"] is False
    assert report["missing"] == ["hardware_enclave"]


def test_registry_accepts_structural_third_party_descriptors():
    custom = AdapterRegistry()
    custom.register(AdapterDescriptor("state", "custom-db", ("snapshot",), "Third-party state adapter."))
    assert custom.check("state", "custom-db", ["snapshot"])["compatible"] is True


def test_adapter_and_capability_api():
    client = TestClient(app)
    listed = client.get("/api/adapters?kind=fault")
    assert listed.status_code == 200
    assert listed.json()["adapters"][0]["name"] == "simulated"
    checked = client.post("/api/adapters/conformance", json={"kind": "state", "name": "memory", "required_capabilities": ["snapshot", "restore"]})
    assert checked.status_code == 200
    assert checked.json()["compatible"] is True
    capabilities = client.get("/api/runtime/capabilities").json()
    assert capabilities["action_schema"] == ACTION_SCHEMA_VERSION


def test_simulated_world_emits_canonical_actions_without_losing_legacy_fields():
    simulator = SimulationEngine()
    trial = simulator.start_trial({}, [{"name": "lookup", "simulation": {"result": {"ok": True}}}], [], 7)
    asyncio.run(simulator.tool_call(trial, trial.tools["lookup"], {"id": "c1"}, [], __import__("random").Random(7)))
    event = trial.events[-1]
    assert event["type"] == "tool.call"
    assert event["kind"] == "call"
    assert event["schema_version"] == ACTION_SCHEMA_VERSION
    assert event["data"]["input"] == {"id": "c1"}


def test_verifier_refuses_to_silently_weaken_required_adapter_boundary():
    execution = {
        "contract": {"policies": [{"type": "deny", "target": {"kind": "delete"}}], "requires": {"state": ["snapshot", "restore"]}},
        "events": [{"kind": "read", "name": "lookup"}],
        "adapters": {"state": "unknown-store"},
    }
    result = verify_runtime_execution(execution)
    assert result["verdict"] == "INCOMPLETE"
    assert result["adapter_conformance"][0]["compatible"] is False


def test_malformed_adapter_requirements_are_incomplete_not_an_exception():
    result = verify_runtime_execution({"contract": {"policies": [{"type": "deny", "target": {"kind": "delete"}}], "requires": ["snapshot"]}, "events": [{"kind": "read"}]})
    assert result["verdict"] == "INCOMPLETE"
    assert result["unresolved"][0]["adapter"] == "requirements"
