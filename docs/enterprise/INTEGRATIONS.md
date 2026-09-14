# Integrations and actual qualification level

## Do not confuse adaptation with isolation

The common simulator owns Worlds, tool schemas, state, faults and trajectories. Framework adapters translate requests/results; they do not generally prevent code from making direct calls. The OpenAI Agents bridge replaces supported function tools on a copied agent and rejects unsupported handoffs/MCP paths rather than claiming to sandbox them. Generic adapters require you to supply World-backed proxy tools. Run only trusted code locally unless a separately validated sandbox/network boundary is present.

| Framework / path | Package extra | Implementation | Verification in this build |
|---|---|---|---|
| Plain Python callable | none | Direct request/runtime adapter | Real local examples and repeated/concurrent suites |
| HTTP agent | none | Actual HTTP request adapter | Real loopback HTTP server |
| Command agent | none | Subprocess adapter | Real process execution |
| Anthropic | anthropic | Tool-use loop backed by World | Fake-client contract tests; no live provider |
| OpenAI Agents | openai-agents | FunctionTool bridge and fail-closed rebinding | SDK-shaped contract tests; native dependency absent |
| LangChain / LangGraph | langchain | Runnable adapter + native proxy tool factory | Contract tests; framework/model execution not qualified |
| PydanticAI | pydantic-ai | Adapter + native proxy tools | Contract tests; native matrix not executed |
| Google ADK | google-adk | Adapter/proxy path | Contract tests; native matrix not executed |
| CrewAI | crewai | Adapter/proxy path | Contract tests; native matrix not executed |
| AutoGen | autogen | Adapter/proxy path | Contract tests; native matrix not executed |
| LlamaIndex | llamaindex | Adapter/proxy path | Contract tests; native matrix not executed |
| smolagents | smolagents | Adapter/proxy path | Contract tests; native matrix not executed |
| Strands | strands | Adapter/proxy path | Contract tests; native matrix not executed |
| MCP | mcp | Low-level schema-preserving server, session-scoped worlds | Contract tests; native SDK v2 package absent |
| OTLP/OpenInference | otel | JSON/protobuf/gRPC span normalization | Real local HTTP/protobuf/gRPC tests |
| TypeScript | separate SDK | Telemetry and versioned REST client | Strict build, Node tests and real HTTP API |

Install extras from this source checkout, e.g. `python -m pip install -e '.[openai-agents]'`, then read the adapter module examples. Do not assume `pip install ordeal-agent` points at this unpublished build. CI has one native dependency job per framework, but some tests only check import/tool construction; none is a paid-model E2E certificate. Pin actual versions after validating your agent, tools, handoffs and instrumentation coverage.

## Production capture

Use Python `PlatformClient.trace`/span contexts or the TypeScript TraceSession to instrument tools/models. Preserve explicit parent relationships; report only usage you measured. A context that exports successfully does not prove every concurrent task has finished. Finish child tasks before ending/flushing a trace. Export failures are fail-open by default; monitor `last_error` or use fail-closed export in test environments. Redaction is defense in depth, not a reason to transmit prohibited data.

OTLP users point their own exporter to the configured endpoint and include bearer/project headers. Resource/span attributes for OpenInference and common model usage keys are normalized where understood; unsupported metadata is preserved as data, not fabricated into a semantic guarantee. OTLP operations are authenticated but no trace-signing agent or hardware source attestation is provided.

## Provider rate limiting

`ProviderLimiter` is opt-in around `httpx.AsyncClient` calls. Configure concurrency, QPS and optional token reservation budget. It honors bounded Retry-After/backoff and reduces concurrency after rate limiting. Requests only retry when you explicitly set `idempotent=True`. This is per-process, so multiple runners need an external shared quota service or conservative allocation; automatic provider account limit discovery is not implemented.

## Remote evaluators and connectors

The common outbound client validates host allowlists, addresses, size/time bounds and redirect behavior. LLM judges use an OpenAI-compatible JSON response contract; compatibility with a named provider must be tested. Secrets should be vault references. Remote service tests in this build use local fake endpoints and do not imply successful calls to paid accounts.

Connector types include generic signed webhook, Slack, Teams, PagerDuty, GitHub status/issue, GitLab status, Jira, Linear, ServiceNow and SMTP. Configure real URLs, project IDs, field requirements and credentials using the resource schema. Delivery state is durable and retryable; payloads are not guaranteed valid for every vendor plan/version. Test in the vendor sandbox before enabling incident or issue creation in production.

Official interface references used during development: https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/ ; https://openai.github.io/openai-agents-python/tools/ ; https://arize-ai.github.io/openinference/spec/ ; https://docs.github.com/en/rest/commits/statuses
