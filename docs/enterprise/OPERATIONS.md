# Operations, recovery and incident runbooks

## Startup and steady state

Keep source/image version, DB schema revision, dependency lock, master-key identity and resource policy versions in the deployment record. Run `ordeal-server migrate` under a single operator-controlled migration job before serving a new schema. Start API, evaluator worker, scheduler and customer runners independently. Verify `/healthz`, `/readyz`, authenticated `/metrics`, a test trace and a test job. Protect public ingress and restrict DB/object storage access before admitting real data.

Observe request/error counts and latency, process memory, SQL locks, disk space, queue age/lease expiration, evaluator spend, delivery retries and retention backlog. The included API gauges count tenant-scoped stored traces, jobs and pending deliveries; request/error rates and latency must be derived from structured logs or your proxy. Alert on service absence externally. There are no published availability/latency SLOs in this release. Define them only after measuring a representative deployment.

## Local encrypted backup and restore

The supplied backup CLI supports SQLite plus the local artifact store, with an authenticated manifest and encrypted payloads. It is bounded and intended for small installations, not a streaming TB-scale backup service. Keep the master key in a separate backup with access controls.

```bash
ordeal-server backup /secure-backups/ordeal.snapshot
ordeal-server restore /secure-backups/ordeal.snapshot /restore/ordeal
```

The restore destination must be empty/new; authentication/integrity failure must stop the restore. Point `ORDEAL_DATA_DIR` at the restored directory and configure the same master key (or `_FILE`) before startup. Verify a representative trace, artifact, dataset history, audit checkpoint and queued job. Use a separate isolated deployment for drills; do not overwrite production while experimenting.

For PostgreSQL use a reviewed database backup/PITR process; for S3 enable appropriate versioning/retention/backup policies. Those procedures are operator work, not supplied by the local backup command. Coordinate DB and object-store recoverability. The package has no measured customer RPO/RTO or cross-region recovery guarantee. Record your measured loss/recovery times after a drill.

## Upgrade and rollback

0.3.0 is the first server schema; the v0.2 package was a local SDK, not an earlier server database. Existing SDK usage now reports missing costs/tokens as unknown, and scenarios with no assertions/budgets are INCOMPLETE. Review CI baselines rather than silently overwriting them. The schema is frozen in Alembic v1; there is no historical server upgrade chain to claim has been tested.

Before upgrading, back up data/key, test in staging, verify artifact/dependency digests, then migrate and restart. A schema-version mismatch should fail closed. Destructive downgrade is intentionally not provided. Restore the compatible backup and matching code if rollback is necessary. Zero-downtime or N-to-N+1 guarantees require real future migration tests.

## Stuck or repeated jobs

Check runner labels, exact local path allowlist, token scope/expiry, queue budget and not_before. An active lease has a worker and expiry; a disconnected worker loses its token after expiry, and another attempt can claim. A stale worker cannot publish completion. Do not manually change lease tokens in production. Cancel from the API/console and confirm the runner observed the cancellation; already-completed external side effects remain. Set downstream idempotency keys before relying on automatic retry.

## Delivery failures

Check allowlisted endpoint/port/DNS, correct secret reference, response size, vendor permissions and schema. A 5xx/429/transport failure may retry with backoff; permanent 4xx or missing/deleted dependencies become dead-letter rather than silently disappearing. Review the cause before explicit retry. Receivers should persist the event ID to deduplicate. Do not replay an issue-creation/payment side effect without checking whether the first delivery succeeded remotely.

## Identity incidents

Disable the affected principal, revoke its tokens, rotate exposed credentials and review audit/export activity. Deprovisioning must be verified against a real authenticated request. If the master key is exposed, assume vault/artifact confidentiality is lost; plan a deliberate re-encryption/recovery process rather than changing the environment key in place. If an identity email changes, review issuer/subject binding; never bypass it to resolve a recycled-email mismatch without authenticating the person.

## Data leak or deletion incident

Stop further export/ingestion if necessary; preserve relevant signed checkpoints externally. Scope affected tenants/resources, revoke compromised secrets, inspect captured inputs/outputs and all downstream copies. Legal holds override automated deletion; do not use retention jobs to destroy incident evidence. Follow the organization's actual notification/legal process. This repository does not supply legal advice or a staffed incident response team.

## Retention and financial operations

Run the scheduler and monitor deletion/backlog outcomes. Configure trace/artifact retention separately and include backups, exports and object-store versions in the policy. Usage statement drafts use integer micro-USD rates; they are not payment collection or accounting-certified invoices. Do not promise budget caps cover unreported provider spend. Compare cost telemetry with actual provider bills.
