terraform {
  required_version = ">= 1.5.0"
}

variable "server_url" {
  type = string
}
variable "project_id" {
  type = string
}
variable "manifest_path" {
  type = string
}

# This is NOT a native Terraform provider. It is a non-destructive manifest
# provisioner using an already installed ordeal-server CLI. The API token is
# inherited from ORDEAL_API_KEY or ORDEAL_API_KEY_FILE, not stored in TF state.
resource "terraform_data" "ordeal_manifest" {
  triggers_replace = [filesha256(var.manifest_path), var.server_url, var.project_id]
  provisioner "local-exec" {
    command = "ordeal-server apply \"$ORDEAL_MANIFEST_PATH\""
    environment = {
      ORDEAL_SERVER_URL = var.server_url
      ORDEAL_PROJECT_ID = var.project_id
      ORDEAL_MANIFEST_PATH = abspath(var.manifest_path)
    }
  }
}

output "manifest_checksum" {
  value = filesha256(var.manifest_path)
}
