provider "google" {
  project = var.project_id
  region  = var.region
}

module "meridian_stack" {
  source = "./modules/meridian-stack"

  project_id = var.project_id
  region     = var.region

  service_account_id = var.service_account_id

  gcs_bucket              = var.gcs_bucket
  create_bucket           = var.create_bucket
  bucket_force_destroy    = var.bucket_force_destroy
  gcs_models_prefix       = var.gcs_models_prefix
  optimization_gcs_prefix = var.optimization_gcs_prefix

  artifact_registry_repo = var.artifact_registry_repo
  enable_gpu_job         = var.enable_gpu_job

  # Repo root — submitted to Cloud Build and hashed for image tags. path.root is
  # deploy/terraform, so ../.. is the repository root.
  build_context = abspath("${path.root}/../..")

  optimization_allowed_tiers = var.optimization_allowed_tiers
  optimization_default_tier  = var.optimization_default_tier
  allow_unauthenticated      = var.allow_unauthenticated

  labels = var.labels
}
