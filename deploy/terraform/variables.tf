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

variable "optimization_allowed_tiers" {
  type    = string
  default = "cloud_cpu"
}
variable "optimization_default_tier" {
  type    = string
  default = "auto"
}
variable "allow_unauthenticated" {
  type    = bool
  default = false
}
variable "labels" {
  type    = map(string)
  default = {}
}
