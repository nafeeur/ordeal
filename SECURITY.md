# Security model

## Production defaults

Production deployments must set `ORDEAL_AUTH_DISABLED=false`. The provided production Compose and Kubernetes examples do this.

Ordeal supports hashed API keys with roles:

- viewer: read results and artifacts;
- operator: create/update tests and execute campaigns;
- admin: issue keys and read audit records.

Raw API keys are shown only at creation time; only SHA-256 hashes are stored.

## Enterprise SSO

The included build provides API-key RBAC as the portable authentication primitive. For SAML/OIDC/SCIM enterprises, place Ordeal behind an identity-aware gateway (Keycloak, Authentik, Okta Access Gateway, Cloudflare Access, oauth2-proxy, etc.) and issue scoped service keys to the UI/worker tier. Native vendor-specific SAML/SCIM provisioning is intentionally not hardcoded into the core.

## Isolation

Untrusted agent code should not be run inside the API process. Use customer-hosted HTTP agents or isolated worker pools/containers. The default release does not enable arbitrary command execution.

## Secrets

Never put model/provider secrets in agent JSON. Agent specs reference environment variable names (`api_key_env`). Kubernetes/Compose secrets inject the actual value into the worker that needs it.

## Network controls

Use namespace/network policies or host firewalls to restrict worker egress. Keep the PostgreSQL service private. Put TLS at the ingress/load balancer. Do not expose the worker claim/complete endpoints publicly without authentication.

## Auditability

Control-plane mutations and API-key issuance write audit records. Trial state changes are separately captured in the world ledger with deterministic hashes.
