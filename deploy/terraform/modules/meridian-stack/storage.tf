resource "google_storage_bucket" "models" {
  count = var.create_bucket ? 1 : 0

  project                     = var.project_id
  name                        = var.gcs_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = var.bucket_force_destroy
  labels                      = var.labels

  depends_on = [google_project_service.services]
}
