# Ordeal 1.1.0 Architecture

Ordeal is an adversarial testing fabric for autonomous software. The core invariant is: **models may propose observations or actions; deterministic code owns canonical state and pass/fail truth whenever the property is expressible in code.**

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

A distributed run is decomposed into immutable `trial.execute` jobs. Each job contains snapshots of the agent, world and scenario plus seed. Completed trial artifacts are aggregated into the source run.

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

- coverage-guided fault search tracks novel final-state hashes;
- deterministic assertions define invariants;
- delta-debugging shrinker removes unnecessary setup/faults while preserving failure;
- causal analysis identifies failed assertions, fault events, first divergence and last consequential world mutation;
- exact replay compares final state hash and verdict;
- incidents can be promoted into regression scenarios.

## CI and model comparison

Runs export JUnit XML. The CLI can enforce pass-rate thresholds. Multiple agent/model versions can be run against identical suites and seeds, then compared by behavioral verdict and final-world hash.

## Apache Kafka execution data plane

Ordeal uses **Apache Kafka** as the production execution/event backbone. Postgres remains the durable control-plane source of truth for runs, workers, jobs, security metadata, and audit state.

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
