# API and reproducible workflows

The self-hosted server generates `/openapi.json` (interactive CDN-based API documentation is intentionally not bundled); the packaged `release-evidence/openapi.json` records this release's API. REST resources use `/api/v1`. Except health/auth bootstrap routes, clients authenticate with a bearer token or server-issued browser session. Machine clients should use project-scoped service accounts. Do not put tokens in query strings.

## Core resource flow

Create resources with `POST /api/v1/projects/{project_id}/resources` and `{kind,name,data}`. Kinds include dataset, world, scenario, evaluator, agent, prompt, monitor, connector, automation, experiment, bundle, incident and review_queue. Read a pinned version with `GET /api/v1/resources/{id}?version=N`. Add a version with `POST .../{id}/versions` and `{base_version:N,data:{...}}`; stale base versions return conflict. Promote using `POST .../{id}/promote` with `{environment:"staging",version:N}`. Production promotion requires the appropriate privilege. List endpoints expose bounded limit/offset pagination; clients should not assume one response contains the entire project.

Prompt rendering uses `POST .../{id}/render` with either `{version:N,variables:{...}}` or `{environment:"production",variables:{...}}`. Exact variable names are required. Only `{identifier}` substitution and escaped braces are allowed; attribute access, indexing, conversions and formatting expressions are rejected. The response includes the resolved version and hash for provenance.

## Traces and a lasting regression

Ingest using the Python/TypeScript SDK or `POST /api/v1/projects/{id}/traces`. Native trace IDs are idempotent for identical content and reject conflicting reuse. OTLP uses `/v1/traces` and merges immutable span IDs; conflicting duplicates fail. A producer's declared PASS is a label, not an independent Ordeal verification result.

Create an empty dataset, then `POST /api/v1/traces/{trace_id}/dataset` with dataset ID, base_version, instruction and optional expected output. This creates a new dataset version rather than overwriting history. The trace case contains provenance and `replay_ready`/capture gaps. Use `PlatformClient.replay_dataset(id, version=N, assertions=[...])` to load an eligible pinned cassette into the local simulator. The test suite deliberately rejects incomplete/redacted cassettes and requires an explicit oracle. Upload the resulting report with `client.upload_report(report)` or `ordeal-server upload report.json`.

## Evaluators and online execution

Evaluator resources accept supported `type` definitions; examples and exact validation are in `src/ordeal_platform/evaluators.py`. Deterministic checks include tool_called, tool_not_called, tool_order, state_equals, output_contains, json_schema, latency/token/cost checks. Remote types are http, llm_judge and embedding_similarity. Human creates a pending review result; Python evaluates only on explicitly opted-in private runners with allowed files.

`POST /api/v1/traces/{id}/evaluate` accepts a pinned evaluator ID/version. Project settings can declare `online_evaluators:[{id,version,sample_rate}]`. Sampling is reproducible by trace/evaluator identity, and selected work is a durable job. Run the evaluator worker separately. A test is not successful merely because an evaluator endpoint returned HTTP 200: check the result verdict. Missing evidence is INCOMPLETE; execution failure is ERROR.

`POST /api/v1/evaluators/calibrate` compares human/judge binary labels; see OpenAPI for the exact arrays. Review queue endpoints list, assign and update annotations. Human labels are inputs to calibration, not automatically trustworthy ground truth.

## Jobs, leases and cancellation

Queue a suite with `{kind:"suite",payload:{suite_path:"examples/enterprise/suite.py"},labels:["private"],estimated_cost_usd:0,idempotency_key:"your-request-id"}` at `/api/v1/projects/{id}/jobs`. The runner must advertise compatible labels and locally allow the exact path. The caller cannot request arbitrary imports, shell commands or downloaded code through this endpoint.

Register runners, claim jobs, heartbeat and finish using the SDK/CLI. Claims have unique fencing tokens. Expired or cancelled leases reject stale completion and secret access. Durable completion is idempotent for the same result and rejects conflicting reuse. Execution state `succeeded` means the runner completed; inspect the report verdicts to decide whether the agent passed. A killed worker may cause a retry: side-effecting tools need application-level idempotency keys.

## Automation and integrations

Declarative manifests have `schema:"ordeal.manifest/v1"` and a resources array. `ordeal-server apply` creates/version-updates and never deletes missing resources. Local scenario packs use `ordeal.pack/v1`, a trusted SHA-256 and a restricted resource/evaluator vocabulary. `add-pack` cannot install executable code or remote judge definitions.

Monitors and automations emit events into a durable delivery table. Connector secrets are referenced by name, not embedded in resources. Endpoints for alerts/deliveries support acknowledgement and explicit dead-letter retry. Receivers must deduplicate event IDs and validate webhook signatures/time windows. Configure real vendor fields before enabling issue creation.

## CI semantics

`ordeal-server gate report.json --baseline before.json --min-pass-rate 1 --summary summary.md` needs no server/token. Empty results, execution errors and INCOMPLETE results fail the gate even when a lower pass threshold is allowed. Removed cases and comparable regressions are surfaced; avoid accidentally updating the baseline in the same untrusted PR being evaluated. The behavior CLI also emits JSON/JUnit and HTML diffs. The CI system must enforce the exit code/status.

## Privacy and operations

Trace exports, legal holds/deletes, artifacts, cache, audit and usage endpoints are scope-checked. `/metrics` requires authorization and supplies tenant-scoped database gauges, not an application request histogram. Health checks do not reveal customer data. External exports and backup copies remain subject to your retention/hold policy.

### Evaluation-backed monitors

A `pass_rate` monitor uses explicitly scored trace statuses; unscored OTLP captures do not meet its minimum sample count. For a quality gate based on Ordeal's own evaluator, create a monitor with `metric:"evaluation_failure_rate"`, `evaluator_id`, `evaluator_version`, threshold/operator and minimum_samples. It uses the most recent result per trace for that pinned evaluator and current trace hash; stale snapshot results and INCOMPLETE/ERROR outcomes are not silently counted as passes. Missing coverage produces no numeric verdict; monitor evaluator failures separately.

Uploaded experiment reports emit `experiment.finished` with an explicit gate outcome. Completion-triggered automations only cascade one generation, preventing an automatic experiment -> job -> experiment loop. CI connectors never report success from missing outcome fields: unknown is pending, and explicit failure wins.
