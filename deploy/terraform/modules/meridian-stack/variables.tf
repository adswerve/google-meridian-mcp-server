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
  description = "Provision the GPU (L4) worker job. Disabled by default: the default optimization_tier (cloud_cpu) never invokes it and L4 quota is not guaranteed. To enable: set to true AND set optimization_tier to cloud_gpu (or cloud_auto) AND ensure L4 quota in the region."
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
variable "optimization_tier" {
  type        = string
  description = "Where this deployment runs optimizations: local | cloud_cpu | cloud_gpu | cloud_auto. cloud_auto picks CPU or GPU by problem size. Defaults to cloud_cpu, not the code default of local -- Terraform describes a cloud deployment, and mirroring the code default here would silently move every existing deployment to the local tier on apply."
  default     = "cloud_cpu"
}

variable "optimization_max_parallel" {
  type        = number
  description = "Caps how many Cloud Run Job executions the server will have in flight at once, across whichever tier is active. Over-cap runs are queued (QUEUED), not rejected. Service only -- a job container runs one already-dispatched run and has no notion of siblings."
  default     = 2
}

# --- Analysis worker (server env) ---
variable "analysis_worker_timeout" {
  type        = number
  description = "Seconds the server waits for a synchronous analysis worker before killing it and returning a worker_timeout envelope. The service request timeout is derived from this (plus a delivery margin), so raising it here also raises the request timeout."
  default     = 300
}

# --- Caching ---
variable "result_cache_enabled" {
  type        = bool
  description = "Whether the server caches analysis results (sets RESULT_CACHE_ENABLED). Leave true -- the production-correct default. Set false only for a deployment doing baseline-capture verification, where the harness's client-side RESULT_CACHE_ENABLED=false must be mirrored on the server, so cloud captures are not served from a warm cache and diffed against cold local ones."
  default     = true
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
