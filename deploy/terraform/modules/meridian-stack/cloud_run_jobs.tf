locals {
  worker_env = {
    PERSISTENCE_BACKEND     = "gcs"
    REGISTRY_BACKEND        = "gcs"
    GCS_BUCKET              = var.gcs_bucket
    GCS_MODELS_PREFIX       = var.gcs_models_prefix
    OPTIMIZATION_GCS_PREFIX = var.optimization_gcs_prefix
  }
}

resource "google_cloud_run_v2_job" "cpu" {
  project             = var.project_id
  name                = var.cpu_job_name
  location            = var.region
  deletion_protection = false
  labels              = var.labels

  template {
    template {
      service_account = google_service_account.worker.email
      max_retries     = 0
      timeout         = var.cpu_job_timeout

      containers {
        image = local.image_ref["opt-cpu"]

        resources {
          limits = {
            cpu    = var.cpu_job_cpu
            memory = var.cpu_job_memory
          }
        }

        dynamic "env" {
          for_each = local.worker_env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  depends_on = [google_project_service.services, terraform_data.build]
}

resource "google_cloud_run_v2_job" "gpu" {
  count = var.enable_gpu_job ? 1 : 0

  project             = var.project_id
  name                = var.gpu_job_name
  location            = var.region
  deletion_protection = false
  labels              = var.labels

  template {
    template {
      service_account               = google_service_account.worker.email
      max_retries                   = 0
      timeout                       = var.gpu_job_timeout
      gpu_zonal_redundancy_disabled = true

      node_selector {
        accelerator = "nvidia-l4"
      }

      containers {
        image = local.image_ref["opt-gpu"]

        resources {
          limits = {
            cpu              = var.gpu_job_cpu
            memory           = var.gpu_job_memory
            "nvidia.com/gpu" = "1"
          }
        }

        dynamic "env" {
          for_each = local.worker_env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  depends_on = [google_project_service.services, terraform_data.build]
}
