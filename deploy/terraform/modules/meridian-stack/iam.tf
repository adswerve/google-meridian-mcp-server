resource "google_service_account" "server" {
  project      = var.project_id
  account_id   = "meridian-mcp-server"
  display_name = "Meridian MCP server (Cloud Run service identity)"

  depends_on = [google_project_service.services]
}

resource "google_service_account" "worker" {
  project      = var.project_id
  account_id   = "meridian-opt-worker"
  display_name = "Meridian optimization worker (Cloud Run job identity)"

  depends_on = [google_project_service.services]
}

# Server may launch jobs + read/cancel executions.
resource "google_project_iam_member" "server_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.server.email}"
}

# Server may run jobs that execute AS the worker SA.
resource "google_service_account_iam_member" "server_acts_as_worker" {
  service_account_id = google_service_account.worker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.server.email}"
}

# Both identities read/write the bucket (models + run files).
resource "google_storage_bucket_iam_member" "server_bucket" {
  bucket = var.gcs_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.server.email}"
}

resource "google_storage_bucket_iam_member" "worker_bucket" {
  bucket = var.gcs_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.worker.email}"
}
