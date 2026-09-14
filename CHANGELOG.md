# Changelog

## 0.3.0 - standalone platform implementation

Added a standalone server/API/web console; identity, scope, OIDC and SCIM subset; OTLP HTTP/protobuf/gRPC; immutable resources and production-to-regression datasets; deterministic/remote/human/private-Python evaluation; durable jobs, customer runners, signed integrations, monitors, quotas, secrets/artifacts, local encrypted backup/restore, audit checkpoints, usage statements, TypeScript SDK, deployment/CI templates and operational documentation.

SDK correctness changes: per-world cassette replay cursors; immutable recorded/injected returns; actual schemas for postponed annotations; enum-normalized fail-closed passthrough mode; rejected duplicate tools/unsupported function signatures; bounded concurrency; explicit unknown usage; no-oracle scenarios now INCOMPLETE; lost-metric regressions are surfaced. Review baselines for these intentional semantic changes.

This is not an enterprise GA/certification release. Read the 56-feature matrix and exact testing evidence. No merge with the previous Ordeal repository is required. Version 0.2.0 was the preceding local SDK foundation, not an earlier server database schema.
