<p align="center">
  <strong>ORDEAL</strong><br/>
  <em>Verify what model-native software actually does.</em>
</p>

<p align="center">
  <a href="https://github.com/nafeeur/ordeal/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/nafeeur/ordeal/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-11110f">
  <img alt="Status" src="https://img.shields.io/badge/status-experimental-11110f">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-11110f">
</p>

Ordeal is an open-source runtime verification system for software whose behavior is selected dynamically by models.

Instead of trusting a model's explanation of what it did, Ordeal evaluates an observed sequence of reads, transformations, writes, and service calls against deterministic policies. The result is `PASS`, `FAIL`, or `INCOMPLETE`, accompanied by concrete evidence and a replayable input bundle.

> **Models choose behavior. Ordeal verifies consequences.**

Ordeal is not production-ready and must not be used as a security boundary or connected to live, irreversible systems.

## Why this exists

Traditional backends encode behavior primarily in fixed code paths. In model-native software, behavior can instead emerge from:

```text
model + context + tools + live state + policy
```

A model may decide which databases to read, how to transform records, where to write them, and which services to invoke. Reviewing a final answer or final state is insufficient: the outcome may look correct even when the model accessed forbidden data, skipped authorization, fabricated provenance, or repeated an irreversible action.

Ordeal treats the execution trajectory as the program under test.

## What works today

Ordeal currently has two complementary verification paths.

| Path | Purpose | Status |
| --- | --- | --- |
| **Runtime verifier** | Evaluate a caller-supplied model execution trace against deterministic policies | Experimental |
| **Adversarial simulator** | Exercise agents in stateful test worlds with controlled faults and deterministic constraints | Experimental |

### Runtime verifier

`POST /api/runtime/verify` accepts an execution contract, initial and final state, and a sequence of observed boundary events. It does not invoke a model.

Supported event kinds:

```text
read  write  delete  call  transform  visualize  decision
```

Supported policies:

| Policy | Verifies |
| --- | --- |
| `deny` | A forbidden action did not occur |
| `require_before` | A required check happened before a sensitive action |
| `data_boundary` | Classified data only reached approved services |
| `max_occurrences` | An action stayed within an idempotency or frequency limit |
| `require_dependency` | A write or output declares a causal path to a trusted source |
| `transformation` | Declared copy, concatenation, and constant mappings are exact |
| `state_assertion` | The supplied final state satisfies a deterministic invariant |

The verifier normalizes events, validates ordering and dependency references, computes a content hash chain, evaluates the contract, and returns policy results, counterevidence, state hashes, a stable fingerprint, and a replay bundle.

### Adversarial simulator

The simulator provides:

- stateful worlds with a canonical mutation ledger;
- deterministic assertions and reusable constraints;
- simulated, HTTP passthrough, and OpenAI-compatible agent adapters;
- seeded pre-execution faults and post-commit response loss;
- repeated-run stability and baseline comparison;
- replay, failure shrinking, JUnit export, and incident-to-regression conversion;
- experimental Kafka-backed distributed execution.

The simulator is a test laboratory, not a faithful replica of arbitrary production services. Simulator profiles, distributed execution, and deployment scaffolding remain experimental.

## Terminal interface

![Ordeal terminal interface](docs/images/ordeal-tui.svg)

Ordeal is terminal-native. The Textual TUI includes:

- an overview of runs, workers, constraints, and the current gate;
- campaign creation and run history;
- behavioral timelines and counterevidence inspection;
- replay and failure-shrinking actions;
- a Runtime view for loading and verifying a JSON execution bundle;
- compact terminal layouts, keyboard navigation, and a command palette.

```text
1–5  switch view     r  refresh     :  command palette
R    replay          S  shrink      ?  help      q  quit
```

Set `ORDEAL_ASCII=1` to replace Unicode symbols. Set `ORDEAL_FROZEN_UI=1` or `NO_MOTION=1` to disable interface motion.

## Quick start

Requirements: Python 3.11 or newer.

```bash
git clone https://github.com/nafeeur/ordeal.git
cd ordeal

python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

Start the API:

```bash
make api
```

In another terminal, open the TUI:

```bash
make tui
```

The API is available at `http://localhost:8000`; interactive API documentation is available at `/docs` in development mode.

### Verify an observed runtime trace

With the API running:

```bash
python ordeal_cli.py verify-runtime \
  examples/model-native-runtime/verified.json \
  --fail-on-verdict
```

The included example represents a model that:

1. reads a customer from Salesforce;
2. checks a legal-hold service;
3. transforms the customer record;
4. writes the result to a campaign system; and
5. invokes a messaging service.

Its contract verifies check ordering, data boundaries, declared provenance, field mappings, duplicate contact attempts, and final state.

Run the deliberately unsafe trace to see counterevidence:

```bash
python ordeal_cli.py verify-runtime \
  examples/model-native-runtime/violated.json \
  --fail-on-verdict
```

The unsafe example skips the legal-hold check and contacts the same customer twice. With `--fail-on-verdict`, the command exits with status `2`.

### Run the simulator demo

Seed the built-in mock agent, world, scenarios, and constraint:

```bash
python ordeal_cli.py seed-demo
```

Run the deterministic campaign:

```bash
python ordeal_cli.py run \
  --agent refund-agent \
  --suite refund-regression \
  --seed 41 \
  --repetitions 3 \
  --fail-on-verdict
```

## Runtime input model

A runtime verification request has four principal parts:

```json
{
  "execution": "example-run",
  "contract": {
    "name": "example-contract",
    "policies": []
  },
  "initial_state": {},
  "final_state": {},
  "events": []
}
```

Events may declare `depends_on` relationships to earlier event IDs. These relationships are assertions supplied by the trace producer; Ordeal checks their structure and evaluates provenance policies over them, but it does not independently discover causality.

See [`examples/model-native-runtime/verified.json`](examples/model-native-runtime/verified.json) for a complete contract and trace.

## Verdicts

| Verdict | Meaning |
| --- | --- |
| `PASS` | Every supported policy passed for the supplied observations |
| `FAIL` | A policy failed or the trace had an integrity/ordering problem |
| `INCOMPLETE` | The contract was empty, malformed, or requested unsupported semantics |

A pass is limited to the declared contract and supplied evidence. It is not proof that unobserved behavior was safe.

## Current limitations

- Ordeal accepts traces but does not yet provide a production trace-collection SDK, gateway, or sidecar.
- Runtime verification results are returned to the caller but are not stored as first-class database records.
- Dependency edges are declared by the trace producer; they are structurally checked, not independently inferred.
- The event hash chain is content-addressed, not signed. It only detects mismatch when the expected chain head comes from a trusted source.
- The policy selector and transformation languages intentionally support a small deterministic subset.
- The runtime verifier does not currently inject faults; fault injection belongs to the simulator path.
- Active simulator trials are process-local, and local/distributed execution does not yet have full semantic parity.
- Replay cannot make external model calls or live dependencies deterministic.
- No multi-tenant isolation, production retention model, penetration testing, or reliability certification has been completed.

See [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) for the full readiness assessment.

## Architecture

```text
model runtime
      │
      ▼
instrumented capability boundary
      │
      ▼
caller-supplied canonical events
      │
      ├── ordering and integrity checks
      ├── policy evaluation
      ├── dependency/provenance traversal
      └── state and transformation checks
      │
      ▼
PASS / FAIL / INCOMPLETE
      │
      ▼
counterevidence + fingerprint + replay bundle
```

The FastAPI service hosts both the runtime verifier and the simulator control plane. PostgreSQL is supported for control-plane records. Kafka workers, Kubernetes manifests, and production-shaped Compose files are experimental infrastructure paths rather than production claims.

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for implementation details.

## Configuration

The CLI defaults to `http://localhost:8000`. Configure a different service with:

```bash
export ORDEAL_API_URL=http://your-ordeal-host:8000
export ORDEAL_API_KEY=your-key
```

Authentication is disabled by default for local development. Review [`.env.example`](.env.example) and [`SECURITY.md`](SECURITY.md) before changing that setting or exposing the API.

## Testing

```bash
PYTHONPATH=backend:. pytest -q
```

CI runs the backend suite, compiles the CLI and TUI, checks CLI startup, and runs the terminal-interface tests.

## Repository layout

```text
backend/app/                         API, simulator, policies, runtime verifier
backend/tests/                       engine, enterprise, and runtime tests
examples/model-native-runtime/      passing and failing runtime traces
examples/enterprise-suite/          HTTP-agent simulator example
ordeal_cli.py                        automation-friendly CLI
ordeal_tui.py                        Textual terminal interface
deploy/k8s/                          experimental Kubernetes manifest
ARCHITECTURE.md                      system architecture
PRODUCTION_READINESS.md              limitations and readiness status
SECURITY.md                          security guidance
```

## Contributing

Contributions are welcome. Useful areas include trusted trace collection, policy primitives, connector adapters, provenance validation, regression persistence, simulator fidelity, replay, failure shrinking, and terminal accessibility.

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a pull request.

## License

MIT. See [`LICENSE`](LICENSE).
