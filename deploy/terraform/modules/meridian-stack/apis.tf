locals {
  required_apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "storage.googleapis.com",
    "cloudbuild.googleapis.com",
    "iam.googleapis.com",
  ]
}

resource "google_project_service" "services" {
  for_each = toset(local.required_apis)

  project = var.project_id
  service = each.value

  disable_on_destroy = false
}
