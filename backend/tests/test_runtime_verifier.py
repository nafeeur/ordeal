from copy import deepcopy

from fastapi.testclient import TestClient

from app.main import app
from app.runtime_verifier import verify_runtime_execution


def execution_spec():
    return {
        "execution": "reengage-customer-c1",
        "contract": {
            "name": "customer-reengagement-v1",
            "policies": [
                {
                    "name": "legal-hold-check-before-contact",
                    "type": "require_before",
                    "target": {"tool": "send_message"},
                    "prerequisite": {"tool": "get_legal_hold", "data.active": False},
                    "same_by": {"data.customer_id": "data.customer_id"},
                },
                {
                    "name": "pii-stays-in-approved-services",
                    "type": "data_boundary",
                    "target": {"kind": {"in": ["write", "call"]}},
                    "classifications": ["PII"],
                    "allowed_services": ["campaign"],
                },
                {
                    "name": "campaign-write-has-salesforce-provenance",
                    "type": "require_dependency",
                    "target": {"tool": "upsert_campaign_contact"},
                    "source": {"tool": "read_salesforce_customer"},
                },
                {
                    "name": "customer-mapping",
                    "type": "transformation",
                    "target": {"kind": "transform"},
                    "source": {"tool": "read_salesforce_customer"},
                    "mappings": [
                        {"op": "copy", "from": "data.customer.id", "to": "data.contact.source_id"},
                        {"op": "concat", "from": ["data.customer.first_name", "data.customer.last_name"], "to": "data.contact.full_name"},
                    ],
                },
                {
                    "name": "contact-once",
                    "type": "max_occurrences",
                    "target": {"tool": "send_message"},
                    "group_by": ["data.customer_id"],
                    "value": 1,
                },
                {
                    "name": "campaign-state-recorded",
                    "type": "state_assertion",
                    "assertion": {"type": "state_equals", "path": "campaign.contacts.c1.status", "value": "queued"},
                },
            ],
        },
        "initial_state": {"campaign": {"contacts": {}}},
        "final_state": {"campaign": {"contacts": {"c1": {"status": "queued"}}}},
        "events": [
            {"id": "e1", "seq": 1, "kind": "read", "service": "salesforce", "tool": "read_salesforce_customer", "data": {"customer": {"id": "c1", "first_name": "Ada", "last_name": "Lovelace"}}, "classifications": ["PII"]},
            {"id": "e2", "seq": 2, "kind": "read", "service": "legal", "tool": "get_legal_hold", "data": {"customer_id": "c1", "active": False}},
            {"id": "e3", "seq": 3, "kind": "transform", "tool": "map_campaign_contact", "depends_on": ["e1", "e2"], "data": {"contact": {"source_id": "c1", "full_name": "Ada Lovelace"}}},
            {"id": "e4", "seq": 4, "kind": "write", "service": "campaign", "tool": "upsert_campaign_contact", "depends_on": ["e3"], "data": {"customer_id": "c1"}, "classifications": ["PII"]},
            {"id": "e5", "seq": 5, "kind": "call", "service": "messaging", "tool": "send_message", "depends_on": ["e4"], "data": {"customer_id": "c1"}},
        ],
    }


def test_runtime_contract_passes_with_provenance_and_evidence_chain():
    result = verify_runtime_execution(execution_spec())
    assert result["verdict"] == "PASS"
    assert result["summary"] == {"events": 5, "policies": 6, "passed": 6, "violated": 0, "unresolved": 0, "integrity_problems": 0}
    assert len(result["evidence"]["chain"]) == 5
    assert verify_runtime_execution(result["replay_bundle"])["fingerprint"] == result["fingerprint"]


def test_runtime_contract_fails_unsafe_duplicate_and_bad_transform():
    spec = execution_spec()
    spec["events"][2]["data"]["contact"]["full_name"] = "Wrong Person"
    duplicate = deepcopy(spec["events"][-1])
    duplicate.update({"id": "e6", "seq": 6})
    spec["events"].append(duplicate)
    result = verify_runtime_execution(spec)
    assert result["verdict"] == "FAIL"
    assert {item["policy"] for item in result["violations"]} == {"customer-mapping", "contact-once"}


def test_runtime_contract_rejects_forward_dependency_and_does_not_claim_pass_without_policy():
    spec = execution_spec()
    spec["events"][0]["depends_on"] = ["e5"]
    assert verify_runtime_execution(spec)["verdict"] == "FAIL"
    spec["events"] = []
    spec["contract"]["policies"] = []
    assert verify_runtime_execution(spec)["verdict"] == "INCOMPLETE"


def test_malformed_policy_and_trace_are_incomplete_or_failed_instead_of_crashing():
    spec = execution_spec()
    spec["contract"]["policies"] = [{"name": "broken-transform", "type": "transformation", "target": {"kind": "transform"}, "mappings": [{"op": "copy"}]}]
    result = verify_runtime_execution(spec)
    assert result["verdict"] == "INCOMPLETE"
    assert result["unresolved"][0]["policy"] == "broken-transform"
    spec = execution_spec()
    spec["events"][0]["depends_on"] = "e2"
    assert verify_runtime_execution(spec)["verdict"] == "FAIL"


def test_runtime_api_endpoint():
    response = TestClient(app).post("/api/runtime/verify", json=execution_spec())
    assert response.status_code == 200
    assert response.json()["verdict"] == "PASS"
