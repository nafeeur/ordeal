> **Project status:** Ordeal is an R&D prototype. Production-shaped deployment files and advanced lab features are experimental and are not a production-readiness claim.

## 1.3.0 — Model-native runtime verification

- Reframed Ordeal around deterministic verification for software whose behavior is selected at runtime by models.
- Added `POST /api/runtime/verify` for verifying observed trajectories without invoking a model.
- Added canonical events for reads, writes, deletes, service calls, transformations, visualizations, and decisions.
- Added deterministic policies for ordering, data boundaries, provenance, transformation mappings, idempotency, denied actions, and final state.
- Added tamper-evident event hash chains, stable fingerprints, and self-contained replay bundles.
- Added an honest `INCOMPLETE` verdict when evidence cannot establish a policy claim.
- Added `ordeal verify-runtime <spec.json>` and passing/failing reference traces.

## 1.2.0 - Hardware-agnostic execution

- Removed accelerator-specific worker discovery, labels, deployment scripts, and scheduling assumptions.
- All Ordeal workers are generic Python workers.
- Optional model inference remains supported through OpenAI-compatible HTTP endpoints, whether managed or self-hosted.
- Kafka execution, seeded reruns, trace-derived simulator profiles, heuristic failure analysis, and enterprise controls remain experimental.

# Ordeal 1.0 research milestone

This release expands Ordeal from a local v0 harness into a research prototype with an experimental distributed execution path.

Highlights: experimental CPU workers; OpenAI-compatible agent execution; deterministic + LLM-assisted simulation with schema retry; trace-derived simulator profiles and coarse conformance summaries; OpenAPI/MCP world scaffolding; seeded random fault exploration; setup/fault shrinking; heuristic failure analysis; incident promotion; seeded reruns; JUnit; API-key RBAC foundation; Kubernetes/Compose examples; new Mission Control UI.

Validation performed in the build environment: Python compilation, 7 backend tests, and an end-to-end distributed worker smoke campaign. The Next.js dependency install could not complete because package download access timed out, so the production frontend build must still be run in normal CI (the included GitHub Actions workflow does this).

## 1.0.1 — Experimental Kafka execution fabric

- Apache Kafka is available as an experimental distributed execution/event data plane.
- Added transactional Postgres -> Kafka outbox dispatcher.
- Added capability-specific Kafka job topics and Python consumer-group workers.
- Added Kafka result topic and idempotent Python result aggregator.
- Workers commit offsets only after result publication is durably acknowledged.
- Failed trials re-enter Kafka through the outbox retry path.
- Mission Control UI now exposes the Kafka-backed execution fabric.
- Docker Compose includes an official Apache Kafka KRaft broker for single-host deployment/testing.

## 1.1.0 — Deterministic Scorer Parity

This release hardens Ordeal's deterministic evaluation layer around four principles: world truth, reusable invariants, repeated-run stability, and commit-aware regression attribution.

### Added
- First-class reusable `Constraint` records with version, severity, description, and bindings.
- Bind constraints by scenario name, scenario tag, or suite.
- Constraint parameter references such as `$vars.customer_id` for scenario-specific values.
- New deterministic scorer primitives:
  - `collection_unique_by`
  - `numeric_lte`
  - `numeric_gte`
  - `ledger_after_field_equals`
  - `ledger_no_repeated_path`
- Run-level constraint summaries with bindings executed, upheld counts, violations, and concrete violating scenario/seed references.
- Repeated-run stability analysis: `stable_pass`, `stable_fail`, and `in_variance`.
- State-fingerprint variance reporting to distinguish outcome variance from world-state drift.
- Commit metadata on runs (`commit_sha`, `base_commit_sha`, `baseline_run_id`).
- Commit-aware regression attribution:
  - new regressions
  - fixed scenarios
  - distribution shifts
  - new constraint violations
  - resolved constraint violations
- PR-style gate endpoint that can block only on regressions introduced by the candidate run.
- New `/constraints` UI for the deterministic scorer library.
- Updated attribution UI and run stability/constraint columns.

### Verification
- Backend tests: 12/12 passing.
- Python compilation: passing.
- Frontend source updated; production Next.js build remains dependent on installing npm dependencies in the deployment/CI environment.
