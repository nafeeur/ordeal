# Contributing

Keep the local SDK optional and the platform standalone. Tests must demonstrate behavior, not just count imports. Add a regression for each bug; distinguish fake-provider contract tests from live-service tests. Never weaken an invariant to make a broken test green. Use private runners for customer code, never API-process evaluation of uploaded Python. Missing evidence/usage must not be converted into a passing result or zero cost.

Use `scripts/verify_release.py` after installing test dependencies. Native browser CI should run without the restricted-environment bridge. Do not commit keys, tokens, traces containing real data, `.ordeal`, `.secrets`, private model transcripts or generated database files. Update the capability matrix when a boundary changes. Releases need reviewed dependencies, executed CI, signed provenance under a real publisher identity and a documented compatibility/rollback plan.
