output "service_uri" {
  description = "Base HTTPS URL of the MCP server (append /mcp/ for the endpoint)."
  value       = google_cloud_run_v2_service.server.uri
}

output "server_service_account" {
  value = google_service_account.server.email
}

output "worker_service_account" {
  value = google_service_account.worker.email
}

output "bucket_name" {
  value = var.gcs_bucket
}

output "artifact_registry_repo" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repo}"
}

output "cpu_job_name" {
  value = google_cloud_run_v2_job.cpu.name
}

output "gpu_job_name" {
  value = var.enable_gpu_job ? google_cloud_run_v2_job.gpu[0].name : null
}
