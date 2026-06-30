variable "project_id" {
  type        = string
  description = "Existing GCP project ID to provision into (created out-of-band)."
}

variable "region" {
  type        = string
  description = "Region for all regional resources."
  default     = "us-central1"
}

# --- GCS ---
variable "gcs_bucket" {
  type        = string
  description = "Bucket holding fitted models and optimization run files. No default."
}

variable "create_bucket" {
  type        = bool
  description = "Create the bucket here, or reference an existing one the client owns."
  default     = true
}

variable "bucket_force_destroy" {
  type        = bool
  description = "Allow `terraform destroy` to delete a non-empty bucket (set true only for throwaway test installs)."
  default     = false
}

variable "gcs_models_prefix" {
  type        = string
  description = "Key prefix under the bucket where fitted models live."
  default     = "models/"
}

variable "optimization_gcs_prefix" {
  type        = string
  description = "Key prefix under the bucket for optimization run manifests/state/results."
  default     = "optimizations/"
}

# --- Artifact Registry ---
variable "artifact_registry_repo" {
  type        = string
  description = "Artifact Registry docker repository id."
  default     = "meridian"
}

# --- Images (built out-of-band; full refs incl. tag) ---
variable "server_image" {
  type        = string
  description = "Full image ref for the MCP server, e.g. REGION-docker.pkg.dev/PROJECT/meridian/server:TAG."
}

variable "worker_cpu_image" {
  type        = string
  description = "Full image ref for the CPU optimization worker."
}

variable "worker_gpu_image" {
  type        = string
  description = "Full image ref for the GPU optimization worker."
}

variable "enable_gpu_job" {
  type        = bool
  description = "Provision the GPU worker job (needs L4 quota in the region)."
  default     = true
}

# --- Names ---
variable "service_name" {
  type    = string
  default = "meridian-mcp-server"
}

variable "cpu_job_name" {
  type    = string
  default = "meridian-opt-cpu"
}

variable "gpu_job_name" {
  type    = string
  default = "meridian-opt-gpu"
}

# --- Sizing ---
variable "server_cpu" {
  type    = string
  default = "2"
}

variable "server_memory" {
  type    = string
  default = "2Gi"
}

variable "cpu_job_cpu" {
  type    = string
  default = "4"
}

variable "cpu_job_memory" {
  type    = string
  default = "16Gi"
}

variable "cpu_job_timeout" {
  type    = string
  default = "3600s"
}

variable "gpu_job_cpu" {
  type    = string
  default = "4"
}

variable "gpu_job_memory" {
  type    = string
  default = "16Gi"
}

variable "gpu_job_timeout" {
  type    = string
  default = "3600s"
}

# --- Optimization tiers (server env) ---
variable "optimization_allowed_tiers" {
  type        = string
  description = "Comma-separated tiers the hosted server permits, e.g. cloud_cpu,cloud_gpu."
  default     = "cloud_cpu"
}

variable "optimization_default_tier" {
  type    = string
  default = "auto"
}

# --- Access ---
variable "allow_unauthenticated" {
  type        = bool
  description = "Grant roles/run.invoker to allUsers on the service (needed for the live tooling test; gate behind auth for real clients)."
  default     = false
}

variable "labels" {
  type    = map(string)
  default = {}
}
