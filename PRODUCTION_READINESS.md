# Research and production-readiness status

Ordeal is currently an R&D prototype for local experimentation. The repository includes production-shaped infrastructure examples, but it is not production-ready, enterprise-certified, or suitable as a security boundary.

## Implemented prototype capabilities

- model-native runtime trace verification through `POST /api/runtime/verify`;
- canonical read/write/call/transform/visualize/decision events with causal dependencies;
- deterministic authorization-order, data-boundary, provenance, transformation, idempotency, deny, and final-state policies;
- hash-chained evidence, stable fingerprints, and self-contained replay bundles;
- explicit `INCOMPLETE` verdicts for absent or unsupported contract semantics;
- stateful deterministic simulator and canonical ledger;
- simulated, passthrough and OpenAI-compatible generative tool simulation;
- tool output-schema validation and regeneration retries;
- worlds, scenarios, suites, datasets/seeds, runs and replay;
- deterministic grading and JUnit export;
- agent version/model metadata and run comparison;
- OpenAPI + MCP + trace-assisted world compiler;
- empirical trace -> simulator profiles and coarse conformance summaries; profiles are not yet connected to tool execution;
- seeded random fault exploration with final-state novelty reporting;
- failure shrinking;
- heuristic failure explanation primitives;
- incident -> regression conversion;
- durable distributed jobs with capability routing, leases, retries and workers;
- OpenAI-compatible agent execution for OpenAI-compatible model endpoints;
- API-key RBAC and audit log;
- Kubernetes and production-shaped Compose deployment examples;
- health/readiness probes and CI for backend/terminal-client builds.

## Known blockers before production evaluation

- active trial state is process-local and does not safely support multiple API replicas;
- local and distributed execution do not yet apply identical adapters and reusable constraints;
- the tool-proxy route does not yet use scoped per-trial authentication;
- post-commit response loss is implemented; partial-response and partial-commit fault phases are not;
- replay does not freeze every external dependency or guarantee deterministic model output;
- large artifacts and full state snapshots are stored in database text payloads;
- multi-tenant data isolation, retention controls, and production migrations are not implemented.

## External validation required before a real enterprise launch

No repository can honestly certify these from a sandbox alone. Before exposing Ordeal to customer production traffic, validate:

1. load/soak tests on the actual PostgreSQL cluster and ingress;
2. external model endpoint throughput, rate limits, latency and failure recovery under target load;
3. worker lease contention and database tuning under target concurrency;
4. backup/restore and disaster recovery;
5. TLS, network policies, secret-manager integration and key rotation;
6. penetration testing and dependency/container scanning;
7. SSO/SCIM behavior through the customer's chosen identity gateway;
8. retention/privacy requirements for customer traces;
9. simulator fidelity on real held-out customer traces;
10. sandbox isolation for any untrusted command/code agent workloads.

The code is therefore a research prototype with production-oriented experiments, not a release candidate for enterprise use.

## Kafka production checklist

Before certifying a large enterprise deployment:

- Run Apache Kafka as a 3+ broker KRaft cluster.
- Set replication factor to 3 and `min.insync.replicas` to at least 2 for critical topics.
- Pre-create `ordeal.jobs.*` and `ordeal.results` with partition counts sized to the intended worker concurrency.
- Enable TLS and SASL/SCRAM (or mTLS) across broker/client traffic.
- Put broker storage on durable high-IOPS disks and alert on under-replicated partitions, ISR shrinkage, consumer lag, produce latency, disk utilization, and controller changes.
- Load-test the complete pipeline at the required event rate; Kafka capacity alone does not certify end-to-end throughput.
- Use separate consumer groups for CPU workers, workers, and result aggregators.
- Validate duplicate-delivery/idempotency behavior for every passthrough integration.
