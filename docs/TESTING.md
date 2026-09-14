# Ordeal 0.3.0 release validation

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

Docker daemon execution or sandbox escape testing; Kubernetes deployment; PostgreSQL concurrency/failover/PITR; live S3 storage; Terraform provider execution; native framework/provider matrix; native-browser network/CSP end-to-end tests; live cloud IdP or vendor connector accounts; provider billing; payment processing; sustained soak tests; independently measured RPO/RTO; external penetration testing; CVE/security workflow execution; signed release attestations; SOC 2/ISO audits; and staffed support/SLA delivery.

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
