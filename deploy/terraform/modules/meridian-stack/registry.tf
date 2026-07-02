resource "google_artifact_registry_repository" "meridian" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_registry_repo
  format        = "DOCKER"
  labels        = var.labels

  depends_on = [google_project_service.services]
}
