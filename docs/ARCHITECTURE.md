# Standalone architecture

Ordeal is now its own source tree and product. No module, server or action schema from the previous GitHub repository is required.

```text
Python simulator/CLI                 Python + TypeScript application telemetry
   |                                           |             OTLP collector
   | immutable reports                         |                    |
   +--------------------------+----------------+--------------------+
                              v
                    API + local web console
                 Identity / scope / limits / audit
                              |
       +----------------------+-----------------------+
       |                      |                       |
  SQL metadata          encrypted artifact store    durable jobs
  versions/traces       local filesystem or S3      leases/fencing
       |                                              |
       +--------------+-------------------------------+
                      |
             Evaluator / scheduler workers
                      |
           pinned remote calls / signed deliveries

Customer runner -> outbound polling -> allowlisted local workspace
              -> child process OR restricted Docker -> report -> API
```

The API never executes uploaded Python. An operator explicitly supplies the private runner workspace and allowed suite/evaluator paths. Leases are at-least-once, fenced with per-attempt tokens. Cancellation terminates the child process group; it cannot reverse an external side effect already performed by an agent. Tool integrations are observation/simulation adapters, not universal sandbox boundaries.

One SQL schema contains tenant-scoped metadata and JSON payloads. SQLite uses WAL and serialized transactions; it is the tested small-install profile, not the scale architecture. PostgreSQL code paths use locking and skip-locked claims, but are not live-service qualified in this release. There are no PostgreSQL row-level security policies. The artifact store supports local encrypted blobs and an optional S3 adapter. There is no columnar analytics database, Kafka requirement or Redis requirement.

Resource heads reference immutable numbered versions. Compare-and-swap writes reject stale edits. Environment aliases point at versions; they do not mutate historical content. Reports may pin dataset/evaluator/prompt/agent references. Production snapshots and producer metadata must not be interpreted as trusted proof of complete execution.

OTLP appends spans into a trace snapshot. Parent IDs, arguments and span kinds are supplied by instrumentation. Missing parents are visible and overall completeness remains unknown. The trace-to-dataset conversion builds a cassette only when tool inputs and terminal returns can be matched; missing/redacted captures are marked not replayable. A cassette preserves the observed returns, not the full external world or future model behavior.

Behavior and quality stay separate. Deterministic checks can establish an invariant over observed data. LLM/HTTP/embedding evaluators measure a configured quality criterion. Neither a high score nor an absent error implies universal agent safety. A scenario with no assertions or budgets is INCOMPLETE.
