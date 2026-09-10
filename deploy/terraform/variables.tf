variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
}

variable "service_account_id" {
  type    = string
  default = ""
}

variable "gcs_bucket" { type = string }
variable "create_bucket" {
  type    = bool
  default = true
}
variable "bucket_force_destroy" {
  type    = bool
  default = false
}
variable "gcs_models_prefix" {
  type    = string
  default = "models/"
}
variable "optimization_gcs_prefix" {
  type    = string
  default = "optimizations/"
}

variable "artifact_registry_repo" {
  type    = string
  default = "meridian"
}
variable "enable_gpu_job" {
  type    = bool
  default = false
}

variable "optimization_tier" {
  type    = string
  default = "cloud_cpu"
}
variable "optimization_max_parallel" {
  type    = number
  default = 2
}
variable "analysis_worker_timeout" {
  type    = number
  default = 300
}
variable "analysis_max_response_bytes" {
  type    = number
  default = 4194304
}
variable "allow_unauthenticated" {
  type    = bool
  default = false
}
variable "result_cache_enabled" {
  type    = bool
  default = true
}
variable "labels" {
  type    = map(string)
  default = {}
}
