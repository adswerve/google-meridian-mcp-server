resource "google_cloud_run_v2_service" "server" {
  project             = var.project_id
  name                = var.service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false
  labels              = var.labels

  template {
    service_account = local.manage_sa ? data.google_service_account.deploy[0].email : null

    scaling {
      max_instance_count = 2
    }

    containers {
      image = local.image_ref["server"]

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = var.server_cpu
          memory = var.server_memory
        }
      }

      # Constants
      env {
        name  = "PERSISTENCE_BACKEND"
        value = "gcs"
      }
      env {
        name  = "REGISTRY_BACKEND"
        value = "gcs"
      }
      env {
        name  = "MCP_TRANSPORT"
        value = "streamable-http"
      }
      env {
        name  = "MCP_HOST"
        value = "0.0.0.0"
      }
      env {
        name  = "MERIDIAN_ENABLE_JAX_X64"
        value = "true"
      }
      # Result cache: enabled by default (production-correct). A deployment
      # doing baseline capture verification (spec 8) needs it OFF instead —
      # the harness sets RESULT_CACHE_ENABLED=false in the CLIENT process,
      # which a deployed server never sees, so without disabling it here too,
      # cloud analysis captures would be served from a warm cache and diffed
      # against cold local ones. Set disable_result_cache=true for that case;
      # leave it false (the default) for real client installs.
      env {
        name  = "RESULT_CACHE_ENABLED"
        value = var.disable_result_cache ? "false" : "true"
      }
      # Genuine inputs
      env {
        name  = "GCS_BUCKET"
        value = var.gcs_bucket
      }
      env {
        name  = "GCS_MODELS_PREFIX"
        value = var.gcs_models_prefix
      }
      env {
        name  = "OPTIMIZATION_GCS_PREFIX"
        value = var.optimization_gcs_prefix
      }
      env {
        name  = "OPTIMIZATION_ALLOWED_TIERS"
        value = var.optimization_allowed_tiers
      }
      env {
        name  = "OPTIMIZATION_DEFAULT_TIER"
        value = var.optimization_default_tier
      }
      # Auto-wired from resources / config
      env {
        name  = "CLOUD_RUN_PROJECT"
        value = var.project_id
      }
      env {
        name  = "CLOUD_RUN_REGION"
        value = var.region
      }
      env {
        name  = "CLOUD_RUN_JOB_CPU"
        value = var.cpu_job_name
      }
      env {
        name  = "CLOUD_RUN_JOB_GPU"
        value = var.gpu_job_name
      }
    }
  }

  depends_on = [google_project_service.services, terraform_data.build]
}

# Optional public access for the live tooling test (Task 11). Gate behind auth
# for real client installs.
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  count = var.allow_unauthenticated ? 1 : 0

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.server.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
