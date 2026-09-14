# Ordeal 0.3.0 — standalone agent behavior platform

**Test what an agent does, observe what it did, and turn failures into lasting regression tests.**

Ordeal combines an Apache-2.0 local Python simulator with a self-hosted team server, web console, authenticated telemetry ingestion, versioned datasets, evaluators, and private runners. It does not depend on a hosted account, Kafka, or a proprietary service. The TypeScript client is also included.

This is an implementation release for evaluation and controlled pilots, **not a certification or a claim that every enterprise deployment has been validated**. The [56-feature status matrix](docs/enterprise/CAPABILITY_MATRIX.md) separates working code, deployment templates, unvalidated external integrations, and organizational requirements. Read the [test report](docs/TESTING.md) before treating a feature as production-qualified.

> **A note on this repository.** This branch replaces a previous, unrelated implementation of "Ordeal" (a FastAPI + terminal-UI runtime verifier) that lived on `main` before 2026-09-14. That project is preserved, unmodified, on the [`archive/pre-standalone-2026-09-14`](../../tree/archive/pre-standalone-2026-09-14) branch. Nothing from it was deleted — this is a deliberate replacement of what `main` points to, not a merge of the two.

## What's here

- A local Python simulator: stateful `World`s, tool-calling `Scenario`s, deterministic assertions (`ToolCalled`, `ToolOrder`, `StateEquals`, ...), fault injection, flakiness detection, and baseline regression comparison.
- A self-hosted team server + web console for capturing production traces, versioning them into replayable datasets, running experiments, and queuing private runners.
- **`LLMAgent`** (new): a built-in agent that drives a real model — OpenAI, OpenRouter, or any OpenAI-compatible endpoint — through a `World`'s own tools, with automatic schema generation, rate limiting, retry, and usage/cost tracking. See [below](#llmagent-real-models-against-simulated-worlds).

## Screenshots

The web console after running a real-model evaluation suite (`ordeal-behavior experiment`, four models compared via OpenRouter):

![Experiment detail: 27/27 scenarios passing for openai/gpt-oss-120b, $0.0025 total cost](docs/screenshots/experiment-passing.jpg)
*`openai/gpt-oss-120b` — 27/27 scenario runs pass across payments, IT access, inventory, and a research/publish chain, including three adversarial prompt-injection cases.*

![Experiment detail: 40.7% pass rate for google/gemini-2.5-flash-lite, with several ERROR verdicts](docs/screenshots/experiment-failing.jpg)
*`google/gemini-2.5-flash-lite` — the same suite drops to 40.7% pass. The `ERROR` rows are a real, reproducible `MALFORMED_FUNCTION_CALL` failure from the provider on specific tool schemas, not a harness bug — `LLMAgent` retries automatically and reports the verdict honestly instead of hanging or fabricating a pass.*

## Start locally

Use Python 3.10+ in a virtual environment. This source tree and its wheel are the installation targets; this release has **not** been published to PyPI or npm.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[server,otel]'
ordeal-server init --name 'My organization'
ordeal-server serve
```

Open `http://127.0.0.1:8080`. Sign in using the token printed once by `init`. Save the project ID for SDK/runner use. Initialization creates a private `.ordeal` directory, a restricted `.ordeal/master.key`, and an SQLite database. Preserve that key separately from encrypted backups. Re-running initialization does not erase existing data or reprint old tokens.

For **local behavior testing only**, install `python -m pip install -e .`. Server dependencies are optional.

```bash
ordeal-behavior init
ordeal-behavior run ordeal_tests/test_agent.py
ordeal-behavior run examples/enterprise/suite.py --json report.json --junit report.xml
ordeal-server gate report.json --summary summary.md
```

The enterprise fixture suite covers authorized/denied payments, authorization timeouts, IT access, stock reservation, retry, and a research-review-publication chain. These are simulated applications, not live financial or identity systems.

## LLMAgent: real models against simulated Worlds

Every other agent adapter in this package (`CallableAgent`, `HTTPAgent`, `CommandAgent`) expects you to already have an agent to wrap. `LLMAgent` *is* one: point it at a model and a `World`, and it runs the full OpenAI-compatible tool-calling loop itself — deriving the function-calling schema from the `World`'s own tools (via `tool_manifest`), rate-limiting and retrying requests through the built-in `ProviderLimiter`, recovering from a provider returning a malformed or empty tool call, and rolling prompt/completion tokens and cost into the scenario report automatically.

```python
from ordeal_agent import LLMAgent, Scenario, StateEquals, Suite, ToolCalled, ToolOrder, World, simulated

def lookup_order(args, ctx):
    return ctx.world.get("orders", {}).get(args["order_id"], {"status": "not_found"})

def refund_order(args, ctx):
    orders = ctx.world.get("orders", {})
    order = orders.get(args["order_id"])
    if order is None or order["status"] != "paid":
        return {"ok": False}
    ctx.world.set("orders", {**orders, args["order_id"]: {**order, "status": "refunded"}})
    return {"ok": True}

world = World(
    "support",
    initial_state={"orders": {"A-100": {"status": "paid", "amount": 42}}},
    tools=[
        simulated("lookup_order", lookup_order, description="Look up an order",
                   input_schema={"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}),
        simulated("refund_order", refund_order, description="Refund an eligible order",
                   input_schema={"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}),
    ],
)

agent = LLMAgent(
    world=world,
    model="google/gemini-2.5-flash-lite",       # any OpenAI-compatible model id
    base_url="https://openrouter.ai/api/v1",     # OpenAI, OpenRouter, vLLM, Ollama, ...
    api_key_env="OPENROUTER_API_KEY",
    extra_body={"usage": {"include": True}},     # provider-specific request fields
)

suite = Suite.of("refund", Scenario(
    "refund-paid-order",
    "A customer wants a refund on order A-100. Look it up and refund it if eligible.",
    world,
    assertions=[ToolCalled("lookup_order"), ToolCalled("refund_order"),
                ToolOrder("lookup_order", "refund_order"),
                StateEquals("orders", {"A-100": {"status": "refunded", "amount": 42}})],
))
```

`StateEquals` and `ToolCalled` together catch a real failure mode plain output-matching misses entirely: a model that *says* "I've refunded your order" without ever calling `refund_order`. That exact case — caught against a live model, not a mock — is what motivated adding `LLMAgent`; see `ordeal_tests/test_openrouter_agent.py` and `ordeal_tests/test_enterprise_experiment.py` for the full suites behind the screenshots above, including three prompt-injection scenarios (a tool result embeds a fake "SYSTEM" instruction trying to get the agent to bypass a denial or skip a validation step) and a multi-model comparison via `ordeal-behavior experiment`.

`LLMAgent` also works as an `ordeal-behavior experiment` variant to rank models by pass rate, cost, and latency on the same suite:

```python
from ordeal_agent import Variant

variants = [Variant(m, LLMAgent(world=world, model=m, base_url="...", api_key_env="...")) for m in MODELS]
```

```bash
ordeal-behavior experiment ordeal_tests/test_enterprise_experiment.py --json report.json
```

## Capture production behavior

```python
import os
from ordeal_agent import PlatformClient

with PlatformClient(
    "http://127.0.0.1:8080",
    os.environ["ORDEAL_API_KEY"],
    os.environ["ORDEAL_PROJECT_ID"],
) as client:
    with client.trace("support-agent") as trace:
        with trace.span("lookup_order", arguments={"order_id": "A100"}) as span:
            order = {"paid": True}  # Replace with your application's call.
            span.set_output(order)
        trace.set_output("Order found")
```

The default production status is **unscored**, not PASS. Missing token/cost measurements remain unknown. Export failures are fail-open by default and available as `trace.last_error`; use `fail_open=False` when export failure should raise. Client-side and server-side redaction are configurable and are not universal PII detection.

Existing OpenTelemetry/OpenInference instrumentation can send OTLP/HTTP JSON or protobuf to `/v1/traces`, with `Authorization: Bearer …` and `X-Ordeal-Project: …`. A separate authenticated gRPC listener is available:

```bash
ordeal-server otlp-grpc --address 127.0.0.1:4317
```

Production gRPC requires certificates; see [deployment](docs/enterprise/DEPLOYMENT.md). OTLP snapshots cannot establish whole-execution completeness.

## Run a private worker

Set `ORDEAL_API_KEY` to a scoped runner credential and `ORDEAL_PROJECT_ID` to its project. In a second terminal:

```bash
ordeal-server runner --workspace . \
  --allow-suite examples/enterprise/suite.py \
  --mode trusted-process --label private
```

Queue that suite from **Jobs** in the console. The runner executes a real child process, heartbeats its lease, uploads the report, and stops when cancellation or lease loss is observed. A succeeded job means execution finished; its tests may still have failed.

`trusted-process` is **not a sandbox**. Use only operator-owned code. The Docker backend requires a digest-pinned image and applies resource, filesystem, capability, and network restrictions; it is supplied but was not run against a Docker daemon in this build environment. Do not give the API a Docker socket. Production model calls in private runners require an explicitly designed customer network policy; the supplied Docker mode has no network.

Server-side online evaluators and delivery/maintenance run independently:

```bash
ordeal-server worker
ordeal-server scheduler
```

## Close the production-to-regression loop

Create a dataset in the console, open a captured trace, and choose **Add to dataset**. After reviewing captured inputs and outputs, load its immutable version:

```python
from ordeal_agent import ToolCalled, ToolOrder, Runner

suite = client.replay_dataset(
    dataset_id,
    version=2,
    assertions=[ToolCalled("refund"), ToolOrder("authorize", "refund")],
)
report = Runner().run_suite_sync(your_agent, suite)
client.upload_report(report)
```

Cassettes replay observed tool returns; they do not recreate arbitrary external state or model randomness. Cases containing redacted or missing inputs/outputs are blocked until repaired explicitly. Your assertions are the oracle, not the fact that a production run happened.

## Capabilities

The console includes trace/trajectory inspection, datasets and histories, agent/prompt/world/scenario registries, environment aliases, experiments, evaluation, human review, jobs, runners, monitors, alerts, integrations, access settings, usage statements, and signed audit checkpoints.

The server provides scoped credentials, tenant/project authorization, browser sessions, OIDC sign-in, a SCIM Users/Groups subset, encrypted secrets/artifacts, immutable versions, bounded ingestion, a durable job queue, online evaluator sampling, encrypted local backup/restore, retention/legal holds, quotas, and durable signed webhooks. Code, custom Python, HTTP, embedding, LLM-judge, and human evaluation paths are separate.

[TypeScript SDK](sdks/typescript/README.md) · [API/workflows](docs/enterprise/API.md) · [framework guide](docs/enterprise/INTEGRATIONS.md) · [deployment](docs/enterprise/DEPLOYMENT.md) · [security](docs/enterprise/SECURITY.md) · [operations](docs/enterprise/OPERATIONS.md)

## Known gaps (read before relying on this for anything real)

Verified hands-on while building the example suites above:

- **Only one live-model integration path (`LLMAgent`) has actually been run against a real provider.** The other ten framework adapters (LangChain, CrewAI, AutoGen, LlamaIndex, Strands, Google ADK, OpenAI Agents SDK, pydantic-ai, smolagents, MCP) are type-shaped only — the release explicitly skips the native framework contract matrix.
- **Docker runner backend is untested against a live Docker daemon.** `trusted-process` mode is not a sandbox.
- **Browser/console tests run through a test-only bridge**, not native Chromium navigation — see [`docs/TESTING.md`](docs/TESTING.md).
- **No independent penetration test, SOC 2/ISO process, or enterprise-scale (Postgres/S3/HA) validation.** See the [commercial launch checklist](docs/enterprise/COMMERCIAL_READINESS.md) before selling this as a hosted service.

The full, itemized 56-feature status is in [`docs/enterprise/CAPABILITY_MATRIX.md`](docs/enterprise/CAPABILITY_MATRIX.md).

## Test the release

```bash
python -m pip install -e '.[server,otel,platform-test,browser]'
python -m playwright install chromium
npm install --prefix sdks/typescript
python scripts/verify_release.py
```

In restricted environments where managed Chromium blocks all navigation, the browser suite has an explicit `ORDEAL_BROWSER_TRANSPORT=bridge` mode: the real UI runs in Chromium while a test-only bridge performs real loopback HTTP/cookie handling. That is **not** native browser-network/CSP validation. Normal CI runs without that override.

## Before selling a hosted service

Use the [commercial launch checklist](docs/enterprise/COMMERCIAL_READINESS.md). No SOC 2/ISO report, external penetration-test conclusion, staffed support, payment collection, or uptime guarantee is included. PostgreSQL HA/load/failover, live IdP/provider compatibility, cloud object storage, Docker/Kubernetes, native browser networking, and large-scale retention need validation in the target deployment. The default small-install SQLite profile is not a demonstrated enterprise-scale SaaS architecture.
