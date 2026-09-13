# Ordeal Research Architecture

> **Status:** Ordeal is an R&D prototype. This document describes both the current local implementation and an experimental distributed design. It is not a claim of production readiness.

Ordeal is a model- and infrastructure-agnostic runtime verification layer for autonomous software, with an adversarial simulator for pre-deployment testing. The core invariant is: **autonomous systems may choose actions; deterministic code owns policy, evidence, and pass/fail truth whenever the property is expressible in code.**

## Stable core, replaceable edges

Ordeal does not make a model provider, agent framework, database, queue, container system, or cloud part of its truth model. Technology-specific behavior terminates at five structural protocols:

| Protocol | Responsibility |
| --- | --- |
| `TargetAdapter` | invoke or observe the autonomous system under test |
| `StateAdapter` | snapshot and restore canonical world state |
| `RuntimeAdapter` | execute and schedule canonical actions |
| `FaultAdapter` | inject a named fault at a declared phase |
| `Verifier` | reduce a contract and evidence bundle to a scoped verdict |

Adapters publish machine-readable capabilities. Verification contracts can require capabilities such as `snapshot`, `restore`, `after_commit`, or `replay`; the registry reports incompatibility before execution instead of silently weakening the boundary.

The canonical `ordeal.action/v1` envelope carries action identity, order, kind, name, actor, data, dependencies, classifications, service, adapter, and runtime. Adapter-specific fields are preserved. `ordeal.contract/v1` identifies the deterministic contract boundary.

## Autonomous runtime verification

The runtime path accepts observations from a capability gateway, sidecar, service mesh, or application SDK. Each normalized event describes a read, write, delete, call, transformation, visualization, or decision and can declare causal dependencies on earlier events.

```text
intent + autonomous controller
          │
          ▼
instrumented capabilities
          │
          ├─ databases
          ├─ APIs
          ├─ files
          └─ visualizations
          │
          ▼
canonical event trace
          │
          ├─ authorization order
          ├─ data boundaries
          ├─ causal provenance
          ├─ transformation mapping
          ├─ idempotency
          └─ final-state invariants
          │
          ▼
PASS / FAIL / INCOMPLETE + replay bundle
```

Events are normalized and hash-chained. Policies are deliberately constrained data structures rather than arbitrary code or model prompts. A missing/unsupported policy produces `INCOMPLETE`; structural trace corruption or a critical/major violation produces `FAIL`. A `PASS` is always scoped to the supplied observations and declared contract—it is not a claim that unobserved behavior was safe.

The initial implementation supports seven policy types: `deny`, `require_before`, `data_boundary`, `max_occurrences`, `require_dependency`, `transformation`, and `state_assertion`. `POST /api/runtime/verify` performs verification without invoking the target and returns a self-contained replay bundle.

## Control plane

- FastAPI API and registry for agents, worlds, scenarios, suites, datasets, simulator profiles and runs.
- PostgreSQL in production; SQLite only for local development.
- API-key RBAC (`viewer`, `operator`, `admin`) and immutable audit records for control-plane mutations.
- Durable `Job` records with capability routing, priority, leases, expiry, retries and worker ownership.
- Health/readiness endpoints for orchestration platforms.

## Execution plane

Workers register labels/capabilities and lease jobs. Typical pools:

- `cpu`: deterministic simulator, grading, replay, shrinking, compilation.
- `cpu`: default execution capability. External model inference is invoked over HTTP and is not coupled to worker hardware.

A distributed run is decomposed into `trial.execute` jobs containing snapshots of the agent, world and scenario plus a seed. Completed trial artifacts are aggregated into the source run. This path is experimental and does not yet have full grading and adapter parity with local execution.

## Stateful simulation

Tool modes:

1. deterministic simulated operations: lookup/list/create/update/delete/state_path/template;
2. generative simulation via an OpenAI-compatible endpoint with output-schema validation/retry;
3. passthrough HTTP tools, with declared ledger mutations to mirror side effects;
4. native/unproxied tools remain agent-owned and can be represented in metadata without Ordeal fabricating their effects.

All state transitions pass through the `WorldLedger`, which records before/after values, source, sequence, timestamps, snapshots and state hashes.

## World compiler and learned simulation

`/api/lab/compile-world` accepts OpenAPI, MCP tool descriptions and traces. It produces a reviewable world/tool model instead of silently claiming perfect inference.

`/api/lab/learn-simulator` builds empirical contracts from traces: observed output shapes, outcome frequencies, latency quantiles, state transitions, samples and confidence. `/api/lab/conformance` evaluates held-out traces and reports a fidelity score plus unseen tools.

## Adversarial search and debugging

- seeded random fault exploration reports novel final-state hashes;
- deterministic assertions define invariants;
- delta-debugging shrinker removes unnecessary setup/faults while preserving failure;
- heuristic failure analysis reports failed assertions, fault events, trace divergence and the last consequential world mutation;
- seeded reruns compare final state hashes and verdicts, without guaranteeing deterministic external model output;
- incidents can be promoted into regression scenarios.

## CI and model comparison

Runs export JUnit XML. The CLI can enforce pass-rate thresholds. Multiple agent/model versions can be run against identical suites and seeds, then compared by behavioral verdict and final-world hash.

## Experimental Apache Kafka execution data plane

The prototype can use **Apache Kafka** as an execution/event backbone. Postgres remains the durable control-plane source of truth for runs, workers, jobs, security metadata, and audit state. The current implementation still requires failure-injection, rebalance, recovery, and semantic-parity testing before this design should be treated as production-capable.

```text
API / scheduler
    │
    ├─ Postgres transaction
    │    ├─ Job row
    │    └─ Outbox row
    │
    ▼
Kafka dispatcher
    │
    ▼
ordeal.jobs.cpu / ordeal.jobs.<capability>
    │
    ▼
Python worker consumer groups
    │
    ├─ execute trial
    └─ publish result only after execution
         │
         ▼
ordeal.results
         │
         ▼
Python result aggregator
         │
         └─ idempotent Job + Run aggregation in Postgres
```

### Reliability semantics

* Scheduler writes the Job and Kafka outbox event in the **same database transaction**.
* The dispatcher publishes with `acks=all` and an idempotent Kafka producer.
* Workers disable automatic offset commits.
* A worker commits its consumed offset only **after Kafka acknowledges the result event**.
* The result aggregator is idempotent by Job ID and ignores already-terminal duplicate deliveries.
* Failed jobs below their retry budget are re-enqueued through the transactional outbox.
* Delivery is intentionally **at least once**. Tool adapters that can perform irreversible real-world effects should use idempotency keys or a contained/staging endpoint.

### Scale model

Kafka partitions are the primary unit of horizontal execution parallelism. Production deployments should provision enough partitions for expected worker concurrency and use a minimum three-broker Kafka cluster with replication factor 3. The single-broker Kafka service in `docker-compose.production.yml` is for single-host deployment/testing, not HA enterprise production.

## Constraint engine and commit attribution

The deterministic scorer engine sits after canonical ledger execution and before any optional semantic judgment.

```text
trajectory + canonical final world
             |
             v
      constraint bindings
             |
             v
 deterministic scorer engine
             |
     +-------+--------+
     |                |
   fact             unresolved
 PASS/FAIL             |
                       v
              optional behavior judge
```

A constraint is versioned independently of agent implementation and can bind across hundreds of scenarios. Run aggregation computes repeat stability before commit comparison. The attribution layer compares baseline and candidate distributions and treats a stable baseline pass -> stable candidate fail as a new regression. New constraint violations are also independently blocking signals.
