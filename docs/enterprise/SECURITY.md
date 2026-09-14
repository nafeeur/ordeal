# Security model and deployment boundaries

This document describes controls present in source, not a penetration-test report or certification.

## Assets and trust boundaries

Sensitive assets include traces and their customer payloads, dataset expected values, prompt content, model credentials, audit records, runner workspaces and the master encryption key. Producers can lie or omit events. Authenticated collector traffic is not source attestation. Untrusted tool results may contain prompt injection; Ordeal records/evaluates them but does not make downstream models immune.

The public API must not run uploaded Python. Only explicitly allowlisted files execute on an operator-controlled runner. Even a simulated Python tool can call the filesystem/network from its handler: simulation is an API abstraction, not OS isolation. Generic framework adapters do not automatically replace every framework tool or prohibit direct network calls. Use restricted execution and provider-specific adapter tests for untrusted agents.

## Identity and scope

Bearer credentials are random, hashed at rest, expire and are revocable. Principal/project permissions are checked at the server. Browser login creates a separate short-lived HttpOnly SameSite session, with Secure in HTTPS/production mode. Tokens are not stored in browser localStorage. Cookie-authenticated writes check the request origin. Project selectors in the UI are not authorization controls; the API enforces scope separately. Tests cover foreign tenant/resource references, scoped readers/writers, revocation and last-owner deactivation.

This is application-enforced tenancy, not database row-level security. The current release is not independently approved for shared-tenant hostile workloads. Prefer dedicated deployments for early customers. Custom permission subsets use the fixed permission vocabulary, not arbitrary hierarchical roles.

## OIDC configuration

Provision the human user with the normalized verified email before sign-in, via the admin API or supported SCIM Users endpoint. Set an operator-owned JSON configuration (prefer `ORDEAL_OIDC_CONFIG_FILE`):

```json
{
  "issuer": "https://idp.example.com/tenant",
  "client_id": "ordeal",
  "client_secret": "STORE IN A SECRET FILE, NOT IN GIT",
  "authorization_endpoint": "https://idp.example.com/authorize",
  "token_endpoint": "https://idp.example.com/token",
  "jwks_uri": "https://idp.example.com/keys",
  "tenant_id": "YOUR_ORGANIZATION_ID",
  "group_roles": {"Ordeal Developers": "developer", "Ordeal Administrators": "admin"}
}
```

Allowlist the IdP hosts. Configure the callback as the exact public origin plus `/auth/oidc/callback`. Authorization uses code flow, PKCE S256, server-side expiring state, browser binding and nonce; JWT checks require signed RS256/ES256, issuer, audience, expiry and authorized party when applicable. Verified email plus an active provisioned account is required. The first identity is bound to issuer/subject to prevent silent email recycling. MFA and conditional access are enforced at the IdP, not implemented as a separate Ordeal password system. One OIDC configuration per server is supported; native SAML is not.

SCIM is a Users/Groups provisioning subset under `/scim/v2`; supported features are advertised by ServiceProviderConfig. Bulk, sorting, schema extensions and complete vendor compatibility are not claimed. Role mappings come from operator configuration, not an arbitrary incoming group role. Deprovisioning disables sign-in and revokes tokens. Validate the actual Okta/Entra/etc. requests before promising support.

## Secrets and cryptography

AES-256-GCM encrypts vault values and artifacts with context-associated data. The local backup is authenticated/encrypted and restore verifies integrity before writing into an empty target. The master key is not stored inside the backup and cannot be recovered if lost. Files mounted from an external secret manager are supported; automatic cloud KMS/CMEK wrapping/rotation is not. Changing an encryption-key label or environment variable is not a migration of old data.

Trace/resource SQL payloads are stored as JSON and require encrypted disks, encrypted backups and appropriate DB transport controls. Do not describe the entire system as application-encrypted because secrets/artifacts are encrypted. Audit checkpoints are Ed25519-signed; store/publicize checkpoints outside the administrator's control to make local rewriting detectable. A local chain is not external attestation or trusted time.

## Egress and resource controls

Remote evaluators/HTTP integrations use explicit host allowlists, address validation, pinned DNS resolution, restricted schemes/ports, response limits and timeouts. Loopback, private, link-local and metadata destinations are rejected outside a development-only override. Redirects are not followed. Network allowlisting does not replace network firewalls. SMTP/vendor payloads need their own live deployment review. Body/event limits and per-process request limits are not a fleet-wide denial-of-service guarantee.

The runner has path/extension/symlink checks, output limits, wall deadlines, process-group termination and lease fencing. Docker command construction is tested, not the actual sandbox runtime in this environment. An attacker controlling the host, container daemon, dependency image or allowlisted source can defeat application assumptions.

## Data minimization

Redaction handles common patterns and configured sensitive keys recursively. It cannot prove a payload contains no PII/credentials. For sensitive applications suppress input/output bodies or redact before export, test on representative data and validate retained fields. Replay is deliberately blocked for known redacted/missing captures; reconstruct only sanitized test fixtures. Retention, legal hold, backups, exports and incident evidence need a coherent operator policy.

## Reporting and release controls

No security inbox, bug bounty or incident team is implied. Before publishing, configure a private vulnerability-reporting channel and list its real owner in this document. Do not submit exploit payloads containing customer data to a public issue tracker. The included CI templates scan dependencies/source and generate build attestations when run in a configured repository; this artifact has not been independently signed/pentested or certified. Inspect `../TESTING.md` for executed checks versus proposed external checks.

Protocol references: https://openid.net/specs/openid-connect-core-1_0.html ; https://www.rfc-editor.org/rfc/rfc7644 ; https://opentelemetry.io/docs/specs/otlp/ ; https://docs.docker.com/engine/containers/resource_constraints/
