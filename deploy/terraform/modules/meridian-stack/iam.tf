# The whole stack (Cloud Run service + CPU/GPU jobs) runs as ONE identity.
#
#   service_account_id = ""   -> the project's compute engine default SA. Create
#                               nothing, bind nothing (relies on its Editor grant).
#   service_account_id = "x"  -> create/adopt x@<project>.iam.gserviceaccount.com
#                               in-apply (idempotent) and bind least-privilege roles.

locals {
  manage_sa       = var.service_account_id != ""
  custom_sa_email = "${var.service_account_id}@${var.project_id}.iam.gserviceaccount.com"
}

# Idempotent create-if-missing, mirroring the in-apply gcloud pattern in builds.tf.
# Adopts an existing SA of the same name instead of failing on re-create.
resource "terraform_data" "service_account" {
  count = local.manage_sa ? 1 : 0

  triggers_replace = var.service_account_id

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      if ! gcloud iam service-accounts describe "${local.custom_sa_email}" --project ${var.project_id} >/dev/null 2>&1; then
        gcloud iam service-accounts create ${var.service_account_id} \
          --project ${var.project_id} \
          --display-name "Meridian MCP deployment identity"
        sleep 5
      fi
    EOT
  }

  depends_on = [google_project_service.services]
}

# Read the SA back for its email/name. depends_on defers the read until after the
# create step on the first apply.
data "google_service_account" "deploy" {
  count = local.manage_sa ? 1 : 0

  project    = var.project_id
  account_id = local.custom_sa_email

  depends_on = [terraform_data.service_account]
}

# Launch / read / cancel Cloud Run jobs.
resource "google_project_iam_member" "deploy_run_developer" {
  count = local.manage_sa ? 1 : 0

  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${data.google_service_account.deploy[0].email}"
}

# Run jobs that execute AS this same SA (the service impersonates the job's SA).
resource "google_service_account_iam_member" "deploy_acts_as_self" {
  count = local.manage_sa ? 1 : 0

  service_account_id = data.google_service_account.deploy[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${data.google_service_account.deploy[0].email}"
}

# Read / write the bucket (models + run files).
resource "google_storage_bucket_iam_member" "deploy_bucket" {
  count = local.manage_sa ? 1 : 0

  bucket = var.gcs_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${data.google_service_account.deploy[0].email}"

  depends_on = [google_storage_bucket.models]
}
