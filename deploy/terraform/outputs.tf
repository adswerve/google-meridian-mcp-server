output "service_uri" {
  description = "Base URL of the MCP server. Append /mcp (no trailing slash) for the streamable-http endpoint."
  value       = module.meridian_stack.service_uri
}
output "server_service_account" { value = module.meridian_stack.server_service_account }
output "worker_service_account" { value = module.meridian_stack.worker_service_account }
output "bucket_name" { value = module.meridian_stack.bucket_name }
output "artifact_registry_repo" { value = module.meridian_stack.artifact_registry_repo }
output "cpu_job_name" { value = module.meridian_stack.cpu_job_name }
output "gpu_job_name" { value = module.meridian_stack.gpu_job_name }
