# CI integration templates

These files run the same tested local CLI and emit JSON/JUnit/Markdown. They have not been executed inside GitLab, Jenkins, CircleCI, Azure DevOps, or Bitbucket. Choose supported images and pin them to reviewed digests in your installation. The GitHub Actions workflow is included under `.github/workflows`.

Gate nonzero exit codes must not be ignored. Baseline updates should require review. Do not let untrusted pull requests read a production runner token, execute against production endpoints, or replace protected baselines. Run fork PRs with no secrets and isolated test data. Hosted commit statuses can be sent by a configured `github_status` or `gitlab_status` connector; a failed status alone does not block merging until branch protection requires it.
