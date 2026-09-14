"""Idempotent API provisioning and checksum-verified local JSON scenario packs. No downloaded code runs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def all_resources(client):
    items, offset = [], 0
    while True:
        page = client.request("GET", f"/api/v1/projects/{client.project_id}/resources?limit=200&offset={offset}")
        items.extend(page["items"])
        if not page["has_more"]:
            return items
        offset += len(page["items"])


def apply_manifest(client, manifest):
    if manifest.get("schema") != "ordeal.manifest/v1" or not isinstance(manifest.get("resources"), list):
        raise ValueError("Expected ordeal.manifest/v1 with a resources list")
    existing = {(r["kind"], r["name"]): r for r in all_resources(client)}
    changes = []
    seen = set()
    for spec in manifest["resources"]:
        key = (spec["kind"], spec["name"])
        if key in seen:
            raise ValueError("Duplicate resource in manifest")
        seen.add(key)
        old = existing.get(key)
        if not old:
            result = client.create(spec["kind"], spec["name"], spec["data"])
            action = "created"
        else:
            current = client.request("GET", f"/api/v1/resources/{old['id']}")
            if current["data"] == spec["data"]:
                result, action = current, "unchanged"
            else:
                result = client.request("POST", f"/api/v1/resources/{old['id']}/versions", {"base_version": current["version"], "data": spec["data"]})
                action = "versioned"
        changes.append({"id": result["id"], "kind": key[0], "name": key[1], "version": result["version"], "action": action})
    return {"changes": changes, "destructive": False, "note": "Resources absent from this manifest are never deleted"}


def install_pack(client, path: Path, expected_hash: str):
    raw = Path(path).read_bytes()
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("Scenario pack exceeds 4 MiB")
    actual = hashlib.sha256(raw).hexdigest()
    if actual.lower() != expected_hash.lower():
        raise ValueError("Scenario pack checksum mismatch")
    pack = json.loads(raw)
    if pack.get("schema") != "ordeal.pack/v1":
        raise ValueError("Unsupported scenario pack schema")
    allowed = {"dataset", "world", "scenario", "bundle", "evaluator"}
    for spec in pack.get("resources", []):
        if spec["kind"] not in allowed:
            raise ValueError("Packs cannot provision identities, secrets, connectors, or automation")
        if spec["kind"] == "evaluator" and spec["data"].get("type") in {"python", "http", "llm_judge", "embedding_similarity"}:
            raise ValueError("Imported packs cannot execute code or make network requests")
    result = apply_manifest(client, {"schema": "ordeal.manifest/v1", "resources": pack["resources"]})
    return {"pack": pack.get("name"), "sha256": actual, **result}
