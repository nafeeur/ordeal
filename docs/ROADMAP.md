# Next qualification milestones

The release prioritizes a working agent-behavior loop rather than speculative feature count. Remaining engineering should be driven by a real pilot and its failure evidence.

1. Re-run native browser, native framework, Docker, PostgreSQL, S3, IdP and connector suites in a target environment with those dependencies. Pin versions and record failures; do not equate import-only compatibility tests with live agent E2E.
2. Independent security review of tenant isolation, runner escape/egress, authentication, secret lifetime, backup handling and trace privacy. Add PostgreSQL RLS or stronger dedicated-tenant boundaries before claiming shared-SaaS defense in depth.
3. Capacity/soak/failover and migration drills; select a trace analytics store only after measuring the actual workload. Establish supported limits and measured RPO/RTO rather than advertising arbitrary SLOs.
4. Complete the commercial operations that code cannot supply: support ownership, incident response, payment processing, contracts, privacy documentation and independent certification where required.
5. Extend the narrower features only as demanded: semantic trajectory clustering, distributed provider limits, native Terraform provider, TypeScript simulator parity, native SAML/CMEK integration and large-scale managed execution.
