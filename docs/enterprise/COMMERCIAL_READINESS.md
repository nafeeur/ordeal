# Commercial launch checklist and non-software work

## What this release can support

Use the working standalone behavior-testing/observability loop for a controlled design-partner pilot with agreed limits, dedicated deployment and trusted runner code. Do not call the source archive an enterprise certification, a hosted service with an SLA, or a fully qualified 56-feature enterprise product. The capability matrix is part of the release, not a marketing disclaimer to remove.

A pilot acceptance test should use the customer's real agent and representative sanitized cases: establish a reviewed baseline, detect an intentionally broken version, capture a production-like failure, create a dataset version, replay it, and prove the regression gate fails. Validate the real IdP deprovisioning, project isolation, token revocation, egress policy, retention/hold and restore procedure before sensitive data is accepted.

## Independent security and certification

An external penetration test, remediated findings and a repeat assessment are separate procurement work. SOC 2 Type I/II or ISO 27001 requires an auditor/certification process and organizational evidence; code cannot issue that report. There is no SOC 2/ISO/PCI/HIPAA badge supplied. Determine actual privacy obligations with qualified counsel and the customer rather than listing every regulation as a feature.

Create and operate controls for access reviews, change approval, endpoint security, staff training, vendor risk, vulnerability response, backups, incident management and business continuity. Assign an owner, frequency, evidence source and reviewer for each control. Use audit records/CI artifacts/restore reports as evidence inputs, not substitutes for operating the controls over time.

## Contracts and data responsibilities

Prepare real service terms, support scope, DPA, subprocessor inventory, retention/deletion commitments, security questionnaire answers, breach notification process and any required customer-specific agreement. Do not promise no subprocessors if the chosen IdP/model/cloud/support providers process data. Deployment geography and data residency depend on actual infrastructure, not this package's logo. No legal terms in this repository create a BAA, warranty or contractual SLA.

## Support and incidents

Name real owners for support, security and operational escalation. Establish a ticket channel, monitoring, severity definitions, backup contacts and incident communications. Commit to response/restore windows only when staffing and measured reliability can meet them. No 24x7 staff, named support engineer, enterprise support email or uptime guarantee has been created here.

## Commercial systems

The software meters operations and produces draft usage statements. A real business still needs customer records, payment processing, subscriptions/credits, tax treatment, invoices, accounting reconciliation and entitlement management. Pricing is a business decision; the source does not establish prices or collect money. Open-source Apache-2.0 applies to the included code; no paid feature has been hidden behind an invented license server.

## Release gate before general enterprise availability

Require real native-browser test results, supported framework version matrix, Docker escape/egress assessment, PostgreSQL/S3 load/failover/restore evidence, native IdP compatibility, reviewed external connectors, capacity/SLO definition, independent security findings closure, signed builds/SBOM, support readiness and customer-facing documentation consistent with reality. Track these as open launch obligations until evidence exists.

## Evidence register template

| Control | Owner | Frequency | Evidence | Reviewer | Status |
|---|---|---|---|---|---|
| Access review | Assign | Define | Principal/token export and review record | Assign | Not operated by this release |
| Restore drill | Assign | Define | Restore timings, integrity checks, data loss assessment | Assign | Local automated tests only |
| Vulnerability management | Assign | Define | Scanner output, triage, remediation, retest | Assign | Workflow template supplied |
| Change approval | Assign | Define | Protected PR, CI evidence, reviewed release digest | Assign | Configure in your repository |
| Incident readiness | Assign | Define | Exercise, contact roster, customer communications | Assign | Organizational work |
