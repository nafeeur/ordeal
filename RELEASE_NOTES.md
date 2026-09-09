## 1.2.0 - Hardware-agnostic execution

- Removed accelerator-specific worker discovery, labels, deployment scripts, and scheduling assumptions.
- All Ordeal workers are generic Python workers.
- Optional model inference remains supported through OpenAI-compatible HTTP endpoints, whether managed or self-hosted.
- Kafka, deterministic testing, replay, learned simulation, causal analysis, and enterprise controls are unchanged.

# Ordeal 1.0 release candidate

This release promotes Ordeal from a local v0 harness to a production-oriented distributed testing fabric.

Highlights: durable CPU workers; OpenAI-compatible agent execution; deterministic + LLM-assisted simulation with schema retry; trace-learned simulator profiles and fidelity scoring; OpenAPI/MCP world compilation; coverage-guided fuzzing; automatic shrinking; causal analysis; incident promotion; replay; JUnit; API-key RBAC/audit; Kubernetes/Compose deployment; new Mission Control UI.

Validation performed in the build environment: Python compilation, 7 backend tests, and an end-to-end distributed worker smoke campaign. The Next.js dependency install could not complete because package download access timed out, so the production frontend build must still be run in normal CI (the included GitHub Actions workflow does this).

## 1.0.1 — Kafka execution fabric

- Apache Kafka is now the production distributed execution/event data plane.
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
