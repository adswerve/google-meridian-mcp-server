variable "project_id" {
  type        = string
  description = "Existing GCP project ID to provision into (created out-of-band)."
}

variable "region" {
  type        = string
  description = "Region for all regional resources."
  default     = "us-central1"
}

# --- Deployment identity ---
variable "service_account_id" {
  type        = string
  description = "Account id (short name, NOT email) of the single service account the Cloud Run service and jobs run as. Empty (default) makes both run as the project's compute engine default SA and creates/binds nothing. Set a name to create/adopt <id>@<project>.iam.gserviceaccount.com in this project and bind least-privilege roles."
  default     = ""
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

# --- Build context ---
variable "build_context" {
  type        = string
  description = "Absolute path to the repo root submitted to Cloud Build and hashed for image tags. Set automatically by the root module; operators never set this."
}

variable "enable_gpu_job" {
  type        = bool
  description = "Provision the GPU (L4) worker job. Disabled by default: the default optimization_allowed_tiers (cloud_cpu) never invokes it and L4 quota is not guaranteed. To enable: set to true AND add cloud_gpu to optimization_allowed_tiers AND ensure L4 quota in the region."
  default     = false
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
