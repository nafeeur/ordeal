<p align="center">
  <strong>ORDEAL</strong><br/>
  <em>Test what an agent does, observe what it did, and turn failures into lasting regression tests.</em>
</p>

<p align="center">
  <a href="https://github.com/nafeeur/ordeal/actions/workflows/behavior-sdk.yml"><img alt="CI" src="https://github.com/nafeeur/ordeal/actions/workflows/behavior-sdk.yml/badge.svg"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-3776AB">
  <img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue">
  <img alt="Status" src="https://img.shields.io/badge/status-evaluation%20%2F%20pilot-orange">
</p>

Ordeal tests what an AI agent actually does, not what it says it did. A Python simulator runs your agent against stateful, tool-calling scenarios and checks the resulting trajectory and final state against deterministic assertions — catching things a plain "did the output look right?" check misses, like an agent claiming to complete an action it never took. A self-hosted server and web console turn captured production traces into versioned regression suites.

Everything runs on your own infrastructure — no hosted account, no Kafka, no proprietary service. A TypeScript client is included for non-Python agents.

> This is an evaluation/pilot release, not a certified production system. See [Known limitations](#known-limitations) and the [capability matrix](docs/enterprise/CAPABILITY_MATRIX.md) before relying on any specific feature.

## Screenshots

The web console after comparing four models on the same suite via `ordeal-behavior experiment`:

| | |
|---|---|
| ![27/27 scenarios passing for openai/gpt-oss-120b, $0.0025 total cost](docs/screenshots/experiment-passing.jpg) | ![40.7% pass rate for google/gemini-2.5-flash-lite, with several ERROR verdicts](docs/screenshots/experiment-failing.jpg) |
| `openai/gpt-oss-120b` — 27/27 runs pass across payments, IT access, inventory, and a research/publish chain, including three adversarial prompt-injection cases. | `google/gemini-2.5-flash-lite` on the identical suite: 40.7% pass. The `ERROR` rows are a reproducible provider-side failure — the harness retries automatically, then reports the verdict honestly instead of hanging or faking a pass. |

## Quickstart

Requires Python 3.10+. Not yet published to PyPI or npm — install from this source tree.

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[server,otel]'
ordeal-server init --name 'My organization'
ordeal-server serve
```

Open `http://127.0.0.1:8080` and sign in with the token `init` prints once. For local behavior testing only, server dependencies aren't required:

```bash
ordeal-behavior init
ordeal-behavior run examples/enterprise/suite.py --json report.json --junit report.xml
```

## LLMAgent: real models against simulated worlds

Point `LLMAgent` at a model and a `World`, and it drives the full tool-calling loop itself — deriving the function-calling schema from the world's own tools, rate-limiting and retrying requests, recovering from a malformed tool call, and tracking token/cost usage.

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

`ToolCalled` and `StateEquals` together catch a model that *says* "I've refunded your order" without ever calling `refund_order` — a failure plain output-matching misses entirely. That's the case that motivated `LLMAgent`; see `ordeal_tests/test_openrouter_agent.py` and `ordeal_tests/test_enterprise_experiment.py` for the full suites behind the screenshots above, and pass a list of `Variant(model, LLMAgent(...))` to `ordeal-behavior experiment` to compare models by pass rate, cost, and latency.

## Beyond local testing

| | |
|---|---|
| **Capture production traces** | `PlatformClient(...).trace(...)` spans, or OTLP/HTTP+gRPC — see [`docs/SDK_GUIDE.md`](docs/SDK_GUIDE.md) |
| **Turn a trace into a regression test** | Console → dataset → `client.replay_dataset(...)` with your own assertions |
| **Run suites on a private worker** | `ordeal-server runner --allow-suite ...` — a real (non-sandboxed) child process; see [`docs/enterprise/DEPLOYMENT.md`](docs/enterprise/DEPLOYMENT.md) |
| **Framework guide, API reference, security, operations** | [`docs/enterprise/`](docs/enterprise/) |

## Known limitations

- All eleven framework adapter shims (LangChain, CrewAI, AutoGen, LlamaIndex, Strands, Google ADK, OpenAI Agents SDK, pydantic-ai, smolagents, MCP, plus `LLMAgent`) now pass CI against the real installed framework packages — three (LangChain, AutoGen, smolagents) had real bugs that silently dropped tool-call arguments or failed schema validation, fixed and verified. Only `LLMAgent` has actually driven a live model end-to-end, though; the others verify the tool-wrapping shim, not a real LLM deciding to call it through that framework.
- The Docker runner backend has been run end-to-end against a live container daemon (job queued → claimed → executed inside an isolated, non-root, read-only, network-disabled container → completed). On an SELinux-enforcing host (Fedora/RHEL-family — common for both Docker and Podman), the bind-mounted workspace is denied at the MAC layer regardless of Unix permissions unless container confinement is relaxed for that mount; `RunnerPolicy` now passes `--security-opt label=disable` for exactly that reason. `trusted-process` mode remains explicitly not a sandbox.
- Browser/console tests run with native Chromium navigation in CI (no transport override) and were independently re-verified locally the same way.
- No independent penetration test, SOC 2/ISO process, or enterprise-scale (Postgres/S3/HA) validation — see [`docs/enterprise/COMMERCIAL_READINESS.md`](docs/enterprise/COMMERCIAL_READINESS.md) before selling this as a hosted service.

Full itemized status: [`docs/enterprise/CAPABILITY_MATRIX.md`](docs/enterprise/CAPABILITY_MATRIX.md) · Test report: [`docs/TESTING.md`](docs/TESTING.md)

## License

Apache License 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
