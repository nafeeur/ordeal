# Ordeal 0.3.0 release validation

## Post-release verification (this branch)

Closed two of the boundaries below after the original release evidence was recorded:

- **Native browser testing.** The "managed Chromium blocks navigation" restriction described below was specific to the original build environment, not universal. CI (`.github/workflows/behavior-sdk.yml`) runs the browser suite with no `ORDEAL_BROWSER_TRANSPORT` override, and it was independently re-verified locally with Playwright's own Chromium against a live local server: 4/4 passed with real navigation, cookies, and network requests — no bridge.
- **Docker daemon execution.** Built the repo's own `Dockerfile` and ran an end-to-end job through `RunnerPolicy`/`CustomerRunner` against a real container daemon (Podman, used as a Docker-CLI-compatible engine): queued a job over the real API, a `docker`-mode runner claimed it, executed the suite inside an isolated non-root/read-only/network-disabled/capability-dropped container, and the job completed with a real report (9/9 scenarios passed). This surfaced a real portability bug: on an SELinux-enforcing host (Fedora/RHEL-family — common for both Docker and Podman deployments, not a Podman-only quirk), the bind-mounted workspace was denied at the MAC layer regardless of Unix file permissions. Fixed by adding `--security-opt label=disable` to the container invocation in `src/ordeal_platform/runner.py`; every other isolation control (no capabilities, no network, read-only root, no-new-privileges, non-root UID) is unaffected.

The optional-framework contract matrix mentioned below has also since been fixed and is fully green in CI: three adapters (LangChain, AutoGen, smolagents) had real bugs — a missing `args_schema` silently dropped tool-call arguments, a missing `__annotations__` broke `typing.get_type_hints()`-based schema inference, and a `**kwargs`-only signature failed smolagents' parameter-name validation — found by actually installing each framework and exercising the shim, not by inspection. See the "Bump" and "Fix CI" commits on `main` for details. This still isn't the same as calling a live model provider through each framework; only `LLMAgent` (added on this branch) has done that.

Sandbox escape testing, Kubernetes deployment, PostgreSQL/S3 live-service validation, and the rest of "Checks not completed here" remain open.

## Actual result

On 14 September 2026, the release runner completed successfully with **166 passing Python tests, one skipped optional-framework test, eight passing TypeScript tests, and nine passing scenarios in the separate enterprise CLI smoke run**. Scenario counts are not additional unit tests: some of these examples are also exercised by the Python suite.

| Check | Actual result | Evidence |
|---|---|---|
| Core SDK, API, security, protocols, runner and operations | 162 passed, 1 skipped; 26.69 seconds | `release-evidence/python-junit.xml` |
| Chromium UI interactions against a real HTTP API through the explicit test bridge | 4 passed; 11.03 seconds | `release-evidence/browser-junit.xml` |
| Strict TypeScript build and Node unit tests | Build passed; 8 tests passed | `release-evidence/release-run.log` |
| Enterprise scenario CLI | 9/9 passed; JSON and JUnit produced | `release-evidence/enterprise-report.json`, `enterprise-junit.xml` |
| Installed Python wheel, separate API and runner processes | All eight workflow phases passed | `release-evidence/installed-smoke.json` |
| Local npm tarball installation/import | Passed offline without registry access | `release-evidence/package-checks.json` |
| Static assets, migrations and type markers inside wheel | Present and importable | `release-evidence/package-checks.json` |
| Deployment-secret setup | Private directory and non-overwrite behavior passed | `release-evidence/package-checks.json` |
| Python source compilation | Passed | `release-evidence/release-run.log` |

The one skipped test is the native optional-agent-framework contract matrix. Installing every framework and calling real model providers was not possible here. Framework-shaped test doubles and routing contracts are **not** substituted for live integration qualification.

## Complete installed-package workflow

`scripts/installed_smoke.py` used the built wheel rather than the source import path. It launched real API, runner and agent child processes, performed HTTP calls over loopback, and checked all of the following:

1. Initialize a tenant/project using the installed CLI; repeat initialization without losing data.
2. Serve packaged static UI files, the API and the transparency page.
3. Capture tool calls with the Python telemetry client; convert a production trace to immutable dataset version 2.
4. Replay the captured tools through a working agent and a deliberately broken agent. Verify the baseline passes and the broken agent causes the installed CI gate to return exit code 1.
5. Queue a suite; complete nine scenarios through the independent private-runner CLI and a real agent subprocess; retrieve the persisted report.
6. Perform 100 distinct synthetic trace ingestions with eight concurrent clients, without errors or duplicate result IDs.
7. Stop/restart the API process and verify trace/job persistence.
8. Create an authenticated encrypted SQLite/artifact backup, restore into an empty directory, start another API process, retrieve the original trace and encrypted artifact, and validate the audit chain. Finally execute the installed local SDK scaffold.

The wheel was installed into a new virtual environment whose dependency path explicitly reused the preinstalled test environment. This demonstrates wheel packaging/import isolation, **not** a fresh online dependency resolution or a self-contained offline dependency bundle. No source-tree `PYTHONPATH` was used by that smoke script.

The TypeScript tarball was installed into a temporary npm project with `--offline --ignore-scripts --no-audit --no-fund`; its exported client and origin validation were exercised. The separate Python integration suite also runs the TypeScript client against a real TCP API.

## Security and adverse-case coverage

Tests include tenant/project boundaries, resource versions and optimistic concurrency, permission subsets, expired/revoked credentials, browser-session origins, signed OIDC JWT validation and browser-bound login state, deprovisioning, wrong-context decryption, audit-chain corruption, backup tampering, legal holds, retention, cache expiry, malformed/oversized/compressed telemetry, unsafe outbound destinations, unknown usage, absent assertions, passthrough safeguards, mutable telemetry snapshots, private directory permissions, delayed/malformed tools, cancellation, stale leases, duplicate completions, budget reservations, large nonmatching job backlogs, and prevention of self-triggering automation loops.

Protocol tests include real local HTTP endpoints for OIDC, LLM-judge/embedding/evaluator responses, and connectors. They validate our request/response handling; they do not establish compatibility with any particular paid provider account, IdP tenant, Jira schema, Slack workspace, or production egress policy.

OTLP/gRPC tests use the actual installed gRPC/protobuf libraries and a real loopback listener. Only traces are implemented; OTLP logs and metrics ingestion are not claimed.

## Browser qualification boundary

The managed Chromium policy in this execution environment blocks normal navigation, including localhost. Browser tests therefore used `ORDEAL_BROWSER_TRANSPORT=bridge`: actual Chromium executes the real HTML/CSS/JavaScript, while a deliberately test-only bridge performs real HTTP requests and cookie handling. Screenshots are in `release-evidence/ui/`.

These tests verify rendering, input, navigation, dataset creation, trace conversion, evaluation, review, version changes, job cancellation, role-sensitive UI, logout, and a 390-pixel-wide layout. **They do not prove native browser cookie behavior, network navigation or CSP enforcement end to end.** Server-side cookie/security headers and origin checks have separate API tests. Run the native browser suite without the override on an unrestricted host before deploying publicly.

Some combined or instrumented invocations hung in this managed environment. The recorded successful release run explicitly disables unrelated auto-loaded pytest plugins and runs browser checks in a separate process. No coverage percentage is claimed: coverage-instrumented attempts did not complete reliably. Two installed-websocket-library deprecation warnings remain in the test output. None is counted as a passing test or hidden as a feature qualification.

## Environment and dependency scope

The executed environment was Linux, Python 3.13.5, Node 22, strict TypeScript compilation, SQLite, real loopback HTTP/gRPC, and Chromium through the restricted transport described above. `dependency-inventory.json` records 47 distributions in the tested core/server/OTLP/S3 dependency profile. `constraints/python313-tested.txt` records their observed versions.

Those constraints are **not** a cryptographic lockfile, CVE scan, universal cross-platform resolver result, or native-framework compatibility guarantee. Installing an S3 client is not testing an S3 service. The declared Python minimum is 3.10; other Python versions have CI matrix configuration but were not executed in this environment. No Rocky Linux 8-specific system installation was tested.

## Checks not completed here

Docker sandbox escape testing (basic daemon execution is now covered — see "Post-release verification" above); Kubernetes deployment; PostgreSQL concurrency/failover/PITR; live S3 storage; Terraform provider execution; live model-provider calls through the ten non-`LLMAgent` framework adapters; native-browser CSP end-to-end tests beyond navigation/cookies/network; live cloud IdP or vendor connector accounts; provider billing; payment processing; sustained soak tests; independently measured RPO/RTO; external penetration testing; CVE/security workflow execution; signed release attestations; SOC 2/ISO audits; and staffed support/SLA delivery.

The local 100-request smoke is deliberately small and uses minimal synthetic traces. Its observed throughput/latency is in `installed-smoke.json`; it is **not** an enterprise capacity benchmark, an availability SLO, or evidence of superiority to another product.

## Reproduce

From the extracted source tree with Python, Node and Chromium available:

```bash
python -m pip install -e '.[server,otel,platform-test,browser]'
python -m playwright install chromium
npm install --prefix sdks/typescript
python scripts/verify_release.py
```

Only when normal Chromium navigation is blocked, make the narrower transport explicit:

```bash
ORDEAL_BROWSER_TRANSPORT=bridge python scripts/verify_release.py
```

For installed-package validation, install `dist/ordeal_agent-0.3.0-py3-none-any.whl` with its server/OTLP dependencies into a separate environment, then run this script using that environment's Python:

```bash
/path/to/wheel-environment/bin/python scripts/installed_smoke.py
```

The release includes CI definitions for these checks and optional native-framework jobs. Their presence is not evidence that a hosted CI service has already run them.
