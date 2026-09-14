# Non-destructive Terraform bridge

Set `ORDEAL_API_KEY` (or `ORDEAL_API_KEY_FILE`) outside Terraform, install the server CLI, then supply `server_url`, `project_id`, and an existing JSON `manifest_path`.

This `terraform_data` provisioner applies a manifest when its file hash, project, or server changes. It does not delete objects, represent individual remote resources in state, import resources, or continuously reconcile drift. Do not market it as a native Terraform provider. For repeatable reconciliation run `ordeal-server apply manifest.json` in CI; updates use compare-and-swap versions. Terraform was not installed in the build environment, so this template was inspected but not plan/apply tested.

Terraform explicitly recommends purpose-built providers over provisioners where available: https://developer.hashicorp.com/terraform/language/provisioners
