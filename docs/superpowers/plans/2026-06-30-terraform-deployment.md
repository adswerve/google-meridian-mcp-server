# Terraform Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a reusable, operator-driven Terraform stack that provisions the entire hosted Meridian MCP server + optimization workers into one GCP project per client, distributed in this open-source repo, then prove it with a live deploy + tooling test + teardown against `as-dev-anze`.

**Architecture:** Two planes. The **build plane** is three documented `gcloud builds submit` commands that push the `server`, `opt-cpu`, and `opt-gpu` images to Artifact Registry — Terraform never builds images. The **provision plane** is a single `meridian-stack` Terraform module (APIs, Artifact Registry, GCS, service accounts + IAM, Cloud Run Service, Cloud Run Jobs) instantiated by a thin root config. Per-client inputs (`terraform.tfvars`, `backend.hcl`) are uncommitted; partial backend config points each client's state at a GCS bucket in that client's own project.

**Tech Stack:** Terraform (HashiCorp `google` provider), Cloud Run v2 (Service + Jobs, L4 GPU), Artifact Registry, GCS, Cloud Build, FastMCP (`streamable-http`), Python 3.12 / `uv` / `pytest` / `ruff`.

## Context7 — REQUIRED for every Terraform task

The Terraform `google` provider evolves quickly (the GPU fields used here — `node_selector`, `gpu_zonal_redundancy_disabled` — are recent). **Before writing or editing any `.tf` file, pull the current resource syntax via the context7 MCP tools** rather than trusting memory or this plan's snippets verbatim:

1. `resolve-library-id` → `/hashicorp/terraform-provider-google`
2. `query-docs` with a specific query, e.g. *"google_cloud_run_v2_service env vars, resources limits, service_account, ingress, deletion_protection"*.

The HCL in this plan was pulled from context7 on 2026-06-30 and is correct as written, but **re-verify the current major version and any field that fails `terraform validate`**. If a field name drifts, context7 is the source of truth.

## Global Constraints

- **Provider pin:** `google` provider `~> 7.0` (latest major as of 2026-06-30 was `7.19.x`). Verify the current major via context7 / the Terraform Registry and pin to it.
- **Terraform required version:** `>= 1.9`.
- **Live-test project:** `as-dev-anze`, region `us-central1` (the only region in this plan with confirmed L4 GPU availability).
- **Resource naming (defaults, overridable):** Artifact Registry repo `meridian`; Cloud Run service `meridian-mcp-server`; jobs `meridian-opt-cpu` / `meridian-opt-gpu`; service account ids `meridian-mcp-server` (server identity) and `meridian-opt-worker` (job identity).
- **GPU sizing:** L4 jobs require **minimum 4 vCPU / 16 GiB** and `nvidia.com/gpu = 1`.
- **`deletion_protection = false`** on every Cloud Run Service and Job (the v2 provider defaults this to `true`; teardown in Task 11 fails otherwise).
- **Env planes (spec §4):** `.env` is local-dev only and is never read in Cloud Run. Deployed env is Terraform-set: constants literal, resource references auto-wired, genuine inputs from `terraform.tfvars`. Prefix vars default `gcs_models_prefix = "models/"`, `optimization_gcs_prefix = "optimizations/"`; `gcs_bucket` has no default.
- **No secrets committed.** `*.tfvars` (except `*.example`), `backend.hcl`, `.terraform/`, `*.tfstate*` are gitignored.
- **Commit message trailer:** none — do not add a Co-Authored-By trailer (project preference).
- **Verify Terraform locally with:** `terraform fmt -check -recursive`, then in the target dir `terraform init -backend=false && terraform validate`. `-backend=false` skips remote state so validation needs no GCP credentials.

---

## File Structure

```
deploy/terraform/
  modules/meridian-stack/
    versions.tf            # required_providers (google ~> 7.0)
    variables.tf           # all module inputs (types + defaults)
    apis.tf                # google_project_service x5
    registry.tf            # google_artifact_registry_repository "meridian"
    storage.tf             # google_storage_bucket (conditional create)
    iam.tf                 # 2 service accounts + project/bucket/SA-user IAM
    cloud_run_service.tf   # google_cloud_run_v2_service (the MCP server)
    cloud_run_jobs.tf      # google_cloud_run_v2_job cpu + gpu
    outputs.tf             # service_uri, SA emails, bucket, job names, repo url
  versions.tf              # required_providers + required_version (root)
  backend.tf               # empty `backend "gcs" {}` for partial config
  main.tf                  # provider "google" + module "meridian_stack" call
  variables.tf             # root variables (mirror module inputs)
  outputs.tf               # passthrough of module outputs
  terraform.tfvars.example # documented placeholders (committed)
  backend.hcl.example      # state-bucket placeholder (committed)
  README.md                # the runbook (Task 10)
scripts/validation/remote_smoke.py   # live tooling test vs deployed URL (Task 9)
tests/unit/test_remote_smoke.py      # offline unit test for its url helper (Task 9)
.gitignore                           # add Terraform ignores (Task 7)
deploy/README.md                     # reframed: build here, provision via TF (Task 10)
README.md                            # add Deployment section (Task 10)
AGENTS.md                            # add Deployment (Terraform) pointer (Task 10)
deploy/deploy_jobs.sh                # DELETED (Task 8)
```

---

## Task 1: Module scaffold — provider pins + all input variables

**Files:**
- Create: `deploy/terraform/modules/meridian-stack/versions.tf`
- Create: `deploy/terraform/modules/meridian-stack/variables.tf`
- Create: `deploy/terraform/modules/meridian-stack/outputs.tf` (empty placeholder for now)

**Interfaces:**
- Produces: the module input contract every later task fills in. Variable names used downstream: `project_id`, `region`, `gcs_bucket`, `create_bucket`, `bucket_force_destroy`, `gcs_models_prefix`, `optimization_gcs_prefix`, `artifact_registry_repo`, `server_image`, `worker_cpu_image`, `worker_gpu_image`, `enable_gpu_job`, `service_name`, `cpu_job_name`, `gpu_job_name`, `server_cpu`, `server_memory`, `cpu_job_cpu`, `cpu_job_memory`, `cpu_job_timeout`, `gpu_job_cpu`, `gpu_job_memory`, `gpu_job_timeout`, `optimization_allowed_tiers`, `optimization_default_tier`, `allow_unauthenticated`, `labels`.

- [ ] **Step 1: Pull current provider syntax via context7**

Use `resolve-library-id` → `/hashicorp/terraform-provider-google`, then `query-docs`: *"required_providers google latest major version and required_version"*. Confirm the current major (plan assumes `7.x`).

- [ ] **Step 2: Write `versions.tf`**

```hcl
terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}
```

- [ ] **Step 3: Write `variables.tf`**

```hcl
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
```

- [ ] **Step 4: Write a placeholder `outputs.tf`**

```hcl
# Outputs are added in later tasks (service URI, SA emails, bucket, job names).
```

- [ ] **Step 5: Validate the module**

Run: `terraform -chdir=deploy/terraform/modules/meridian-stack init -backend=false && terraform -chdir=deploy/terraform/modules/meridian-stack validate`
Expected: `Success! The configuration is valid.` (no resources yet)
Also run: `terraform fmt -check -recursive deploy/terraform`
Expected: no output (formatted).

- [ ] **Step 6: Commit**

```bash
git add deploy/terraform/modules/meridian-stack
git commit -m "feat(deploy): terraform module scaffold + input variables"
```

---

## Task 2: APIs + Artifact Registry

**Files:**
- Create: `deploy/terraform/modules/meridian-stack/apis.tf`
- Create: `deploy/terraform/modules/meridian-stack/registry.tf`

**Interfaces:**
- Consumes: `var.project_id`, `var.region`, `var.artifact_registry_repo` (Task 1).
- Produces: `google_project_service.services` (map; later resources `depends_on` it), `google_artifact_registry_repository.meridian`.

- [ ] **Step 1: Pull current syntax via context7**

`query-docs`: *"google_project_service disable_on_destroy and google_artifact_registry_repository docker format"*.

- [ ] **Step 2: Write `apis.tf`**

```hcl
locals {
  required_apis = [
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "storage.googleapis.com",
    "cloudbuild.googleapis.com",
    "iam.googleapis.com",
  ]
}

resource "google_project_service" "services" {
  for_each = toset(local.required_apis)

  project = var.project_id
  service = each.value

  disable_on_destroy = false
}
```

- [ ] **Step 3: Write `registry.tf`**

```hcl
resource "google_artifact_registry_repository" "meridian" {
  project       = var.project_id
  location      = var.region
  repository_id = var.artifact_registry_repo
  format        = "DOCKER"
  labels        = var.labels

  depends_on = [google_project_service.services]
}
```

- [ ] **Step 4: Validate**

Run: `terraform -chdir=deploy/terraform/modules/meridian-stack validate && terraform fmt -check -recursive deploy/terraform`
Expected: `Success!` and clean fmt.

- [ ] **Step 5: Commit**

```bash
git add deploy/terraform/modules/meridian-stack/apis.tf deploy/terraform/modules/meridian-stack/registry.tf
git commit -m "feat(deploy): enable required APIs + artifact registry repo"
```

---

## Task 3: GCS bucket (conditional create)

**Files:**
- Create: `deploy/terraform/modules/meridian-stack/storage.tf`

**Interfaces:**
- Consumes: `var.project_id`, `var.region`, `var.gcs_bucket`, `var.create_bucket`, `var.bucket_force_destroy` (Task 1).
- Produces: a bucket named `var.gcs_bucket` (created only when `create_bucket`). **Downstream IAM and env reference `var.gcs_bucket` directly** (the name), so they work whether the bucket is created here or pre-existing.

- [ ] **Step 1: Pull current syntax via context7**

`query-docs`: *"google_storage_bucket uniform_bucket_level_access force_destroy location"*.

- [ ] **Step 2: Write `storage.tf`**

```hcl
resource "google_storage_bucket" "models" {
  count = var.create_bucket ? 1 : 0

  project                     = var.project_id
  name                        = var.gcs_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = var.bucket_force_destroy
  labels                      = var.labels

  depends_on = [google_project_service.services]
}
```

- [ ] **Step 3: Validate**

Run: `terraform -chdir=deploy/terraform/modules/meridian-stack validate && terraform fmt -check -recursive deploy/terraform`
Expected: `Success!` and clean fmt.

- [ ] **Step 4: Commit**

```bash
git add deploy/terraform/modules/meridian-stack/storage.tf
git commit -m "feat(deploy): conditional GCS bucket for models + optimization runs"
```

---

## Task 4: Service accounts + IAM

**Files:**
- Create: `deploy/terraform/modules/meridian-stack/iam.tf`

**Interfaces:**
- Consumes: `var.project_id`, `var.gcs_bucket` (Task 1/3).
- Produces: `google_service_account.server` and `google_service_account.worker` (their `.email` is consumed by Tasks 5 and 6).

**Why these roles:** the server SA calls the Cloud Run Admin API to launch jobs and read/cancel executions (`roles/run.developer`), and to run a job *as* the worker SA it needs `roles/iam.serviceAccountUser` on the worker SA. Both SAs read/write run files and models in the bucket (`roles/storage.objectAdmin`). These are reasonable least-privilege starting roles; tighten later if needed.

- [ ] **Step 1: Pull current syntax via context7**

`query-docs`: *"google_service_account, google_project_iam_member, google_storage_bucket_iam_member, google_service_account_iam_member"*.

- [ ] **Step 2: Write `iam.tf`**

```hcl
resource "google_service_account" "server" {
  project      = var.project_id
  account_id   = "meridian-mcp-server"
  display_name = "Meridian MCP server (Cloud Run service identity)"

  depends_on = [google_project_service.services]
}

resource "google_service_account" "worker" {
  project      = var.project_id
  account_id   = "meridian-opt-worker"
  display_name = "Meridian optimization worker (Cloud Run job identity)"

  depends_on = [google_project_service.services]
}

# Server may launch jobs + read/cancel executions.
resource "google_project_iam_member" "server_run_developer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.server.email}"
}

# Server may run jobs that execute AS the worker SA.
resource "google_service_account_iam_member" "server_acts_as_worker" {
  service_account_id = google_service_account.worker.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.server.email}"
}

# Both identities read/write the bucket (models + run files).
resource "google_storage_bucket_iam_member" "server_bucket" {
  bucket = var.gcs_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.server.email}"
}

resource "google_storage_bucket_iam_member" "worker_bucket" {
  bucket = var.gcs_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.worker.email}"
}
```

- [ ] **Step 3: Validate**

Run: `terraform -chdir=deploy/terraform/modules/meridian-stack validate && terraform fmt -check -recursive deploy/terraform`
Expected: `Success!` and clean fmt.

- [ ] **Step 4: Commit**

```bash
git add deploy/terraform/modules/meridian-stack/iam.tf
git commit -m "feat(deploy): server + worker service accounts and least-privilege IAM"
```

---

## Task 5: Cloud Run Service (the MCP server)

**Files:**
- Create: `deploy/terraform/modules/meridian-stack/cloud_run_service.tf`
- Modify: `deploy/terraform/modules/meridian-stack/outputs.tf`

**Interfaces:**
- Consumes: `google_service_account.server` (Task 4), `var.server_image`, sizing/name/tier/access vars (Task 1), `var.gcs_bucket` + prefixes.
- Produces: `google_cloud_run_v2_service.server` whose `.uri` is an output. Env wires the server to the bucket and to the **job short-names** `var.cpu_job_name` / `var.gpu_job_name` (Task 6 creates jobs with exactly those names — auto-wired, cannot drift).

**Note on PORT:** Cloud Run injects `PORT=8080`; `server.py` reads `PORT` (`int(os.getenv("PORT", ...))`) and binds `0.0.0.0`, so no port var is needed beyond the documented `container_port = 8080`.

- [ ] **Step 1: Pull current syntax via context7**

`query-docs`: *"google_cloud_run_v2_service template containers env resources limits ports service_account ingress scaling deletion_protection"*.

- [ ] **Step 2: Write `cloud_run_service.tf`**

```hcl
resource "google_cloud_run_v2_service" "server" {
  project             = var.project_id
  name                = var.service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false
  labels              = var.labels

  template {
    service_account = google_service_account.server.email

    scaling {
      max_instance_count = 2
    }

    containers {
      image = var.server_image

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

  depends_on = [google_project_service.services]
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
```

- [ ] **Step 3: Append outputs to `outputs.tf`**

```hcl
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
```

- [ ] **Step 4: Validate**

Run: `terraform -chdir=deploy/terraform/modules/meridian-stack validate && terraform fmt -check -recursive deploy/terraform`
Expected: `Success!` and clean fmt.

- [ ] **Step 5: Commit**

```bash
git add deploy/terraform/modules/meridian-stack/cloud_run_service.tf deploy/terraform/modules/meridian-stack/outputs.tf
git commit -m "feat(deploy): cloud run service for the MCP server with wired env"
```

---

## Task 6: Cloud Run Jobs (CPU + GPU)

**Files:**
- Create: `deploy/terraform/modules/meridian-stack/cloud_run_jobs.tf`
- Modify: `deploy/terraform/modules/meridian-stack/outputs.tf`

**Interfaces:**
- Consumes: `google_service_account.worker` (Task 4), `var.worker_cpu_image`, `var.worker_gpu_image`, `var.enable_gpu_job`, job names/sizing/timeouts, `var.gcs_bucket` + prefixes.
- Produces: `google_cloud_run_v2_job.cpu` (always) and `google_cloud_run_v2_job.gpu` (when `enable_gpu_job`). Job short-names equal `var.cpu_job_name`/`var.gpu_job_name` — the exact strings the server's env points at (Task 5).

**GPU shape (verified via context7 2026-06-30):** v2 jobs nest `template.template`; GPU goes via `node_selector { accelerator = "nvidia-l4" }`, a `"nvidia.com/gpu" = "1"` limit, and `gpu_zonal_redundancy_disabled = true` (avoids a zonal-redundancy quota requirement). `max_retries = 0` matches the current executor contract.

- [ ] **Step 1: Pull current syntax via context7**

`query-docs`: *"google_cloud_run_v2_job template template containers node_selector accelerator nvidia-l4 gpu_zonal_redundancy_disabled resources nvidia.com/gpu max_retries service_account timeout deletion_protection"*.

- [ ] **Step 2: Write `cloud_run_jobs.tf`**

```hcl
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
        image = var.worker_cpu_image

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

  depends_on = [google_project_service.services]
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
        image = var.worker_gpu_image

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

  depends_on = [google_project_service.services]
}
```

- [ ] **Step 3: Append job-name outputs to `outputs.tf`**

```hcl
output "cpu_job_name" {
  value = google_cloud_run_v2_job.cpu.name
}

output "gpu_job_name" {
  value = var.enable_gpu_job ? google_cloud_run_v2_job.gpu[0].name : null
}
```

- [ ] **Step 4: Validate**

Run: `terraform -chdir=deploy/terraform/modules/meridian-stack validate && terraform fmt -check -recursive deploy/terraform`
Expected: `Success!` and clean fmt.

- [ ] **Step 5: Commit**

```bash
git add deploy/terraform/modules/meridian-stack/cloud_run_jobs.tf deploy/terraform/modules/meridian-stack/outputs.tf
git commit -m "feat(deploy): cloud run cpu + gpu optimization worker jobs"
```

---

## Task 7: Root config — provider, backend, module wiring, examples, gitignore

**Files:**
- Create: `deploy/terraform/versions.tf`
- Create: `deploy/terraform/backend.tf`
- Create: `deploy/terraform/main.tf`
- Create: `deploy/terraform/variables.tf`
- Create: `deploy/terraform/outputs.tf`
- Create: `deploy/terraform/terraform.tfvars.example`
- Create: `deploy/terraform/backend.hcl.example`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: the `meridian-stack` module (Tasks 1–6).
- Produces: the deployable root. `terraform init -backend-config=backend.hcl` + `apply` is the operator entrypoint.

- [ ] **Step 1: Write `versions.tf`** (same pins as the module, plus the provider block lives in `main.tf`)

```hcl
terraform {
  required_version = ">= 1.9"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }
}
```

- [ ] **Step 2: Write `backend.tf`** (empty block enables partial config at init)

```hcl
terraform {
  backend "gcs" {}
}
```

- [ ] **Step 3: Write `main.tf`**

```hcl
provider "google" {
  project = var.project_id
  region  = var.region
}

module "meridian_stack" {
  source = "./modules/meridian-stack"

  project_id = var.project_id
  region     = var.region

  gcs_bucket              = var.gcs_bucket
  create_bucket           = var.create_bucket
  bucket_force_destroy    = var.bucket_force_destroy
  gcs_models_prefix       = var.gcs_models_prefix
  optimization_gcs_prefix = var.optimization_gcs_prefix

  artifact_registry_repo = var.artifact_registry_repo
  server_image           = var.server_image
  worker_cpu_image       = var.worker_cpu_image
  worker_gpu_image       = var.worker_gpu_image
  enable_gpu_job         = var.enable_gpu_job

  optimization_allowed_tiers = var.optimization_allowed_tiers
  optimization_default_tier  = var.optimization_default_tier
  allow_unauthenticated      = var.allow_unauthenticated

  labels = var.labels
}
```

- [ ] **Step 4: Write root `variables.tf`** (mirror of the module inputs the root exposes)

```hcl
variable "project_id" { type = string }
variable "region" {
  type    = string
  default = "us-central1"
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
variable "server_image" { type = string }
variable "worker_cpu_image" { type = string }
variable "worker_gpu_image" { type = string }
variable "enable_gpu_job" {
  type    = bool
  default = true
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
```

- [ ] **Step 5: Write root `outputs.tf`**

```hcl
output "service_uri" {
  description = "Base URL of the MCP server. Append /mcp/ for the streamable-http endpoint."
  value       = module.meridian_stack.service_uri
}
output "server_service_account" { value = module.meridian_stack.server_service_account }
output "worker_service_account" { value = module.meridian_stack.worker_service_account }
output "bucket_name" { value = module.meridian_stack.bucket_name }
output "artifact_registry_repo" { value = module.meridian_stack.artifact_registry_repo }
output "cpu_job_name" { value = module.meridian_stack.cpu_job_name }
output "gpu_job_name" { value = module.meridian_stack.gpu_job_name }
```

- [ ] **Step 6: Write `terraform.tfvars.example`**

```hcl
# Copy to terraform.tfvars (gitignored) and fill in. Image tags come from the
# three `gcloud builds submit` commands in deploy/terraform/README.md.

project_id = "your-client-project"
region     = "us-central1"

gcs_bucket = "your-client-meridian"
# create_bucket        = true        # set false to reuse an existing bucket
# bucket_force_destroy = false       # true only for throwaway test installs
# gcs_models_prefix       = "models/"
# optimization_gcs_prefix = "optimizations/"

server_image     = "us-central1-docker.pkg.dev/your-client-project/meridian/server:latest"
worker_cpu_image = "us-central1-docker.pkg.dev/your-client-project/meridian/opt-cpu:latest"
worker_gpu_image = "us-central1-docker.pkg.dev/your-client-project/meridian/opt-gpu:latest"
# enable_gpu_job = true              # false if no L4 quota in the region

# optimization_allowed_tiers = "cloud_cpu"   # add cloud_gpu when GPU job is enabled
# allow_unauthenticated      = false          # true only for the live tooling test
```

- [ ] **Step 7: Write `backend.hcl.example`**

```hcl
# Copy to backend.hcl (gitignored). Bucket must already exist in the client's
# project (created in the bootstrap step). Use a per-client prefix.
bucket = "your-client-tfstate"
prefix = "meridian/state"
```

- [ ] **Step 8: Add Terraform ignores to `.gitignore`**

Append:

```gitignore
# Terraform (per-client inputs + state are never committed)
deploy/terraform/**/.terraform/*
deploy/terraform/**/*.tfstate
deploy/terraform/**/*.tfstate.*
deploy/terraform/**/*.tfvars
!deploy/terraform/**/*.tfvars.example
deploy/terraform/**/backend.hcl
!deploy/terraform/**/backend.hcl.example
crash.log
```

- [ ] **Step 9: Validate the root config**

Run: `terraform -chdir=deploy/terraform init -backend=false && terraform -chdir=deploy/terraform validate && terraform fmt -check -recursive deploy/terraform`
Expected: `Success! The configuration is valid.` and clean fmt.

- [ ] **Step 10: Confirm ignores work**

Run: `git status --porcelain deploy/terraform`
Expected: only the committed files (`*.tf`, `*.example`, later `README.md`) appear — no `.terraform/`, `*.tfstate`, real `*.tfvars`, or `backend.hcl`.

- [ ] **Step 11: Commit**

```bash
git add deploy/terraform/versions.tf deploy/terraform/backend.tf deploy/terraform/main.tf \
        deploy/terraform/variables.tf deploy/terraform/outputs.tf \
        deploy/terraform/terraform.tfvars.example deploy/terraform/backend.hcl.example .gitignore
git commit -m "feat(deploy): terraform root config, examples, and gitignore"
```

---

## Task 8: Retire the bash deploy script; the build step is documented commands

**Files:**
- Delete: `deploy/deploy_jobs.sh`
- (No new `.sh`. The three build commands live in the runbook — Task 10.)

**Interfaces:**
- Produces: a repo where image building is three documented `gcloud builds submit` commands and resource provisioning is Terraform-only. Confirms the `Dockerfile` / `deploy/Dockerfile.worker*` entrypoints the images use are unchanged.

- [ ] **Step 1: Confirm the three Dockerfiles exist and their entrypoints match the env contract**

Run: `ls Dockerfile deploy/Dockerfile.worker deploy/Dockerfile.worker.gpu && grep -H "google_meridian_mcp_server" Dockerfile deploy/Dockerfile.worker deploy/Dockerfile.worker.gpu`
Expected: server → `...server`; both workers → `...execution.worker`.

- [ ] **Step 2: Delete the obsolete script**

```bash
git rm deploy/deploy_jobs.sh
```

- [ ] **Step 3: Confirm nothing else references it**

Run: `grep -rn "deploy_jobs.sh" . --exclude-dir=.git || echo "no references"`
Expected: `no references` (the `deploy/README.md` reference is rewritten in Task 10; if it still shows here, Task 10 will remove it).

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(deploy): retire deploy_jobs.sh; terraform owns resources, build is documented"
```

---

## Task 9: Live tooling test script (`remote_smoke.py`) + offline unit test

**Files:**
- Create: `scripts/validation/remote_smoke.py`
- Create: `tests/unit/test_remote_smoke.py`

**Interfaces:**
- Consumes: a deployed server URL (CLI arg `--url` or env `MERIDIAN_MCP_URL`). Tool names verified from `src/.../transport/tools.py`: `list_models`, `get_model_overview`, `run_optimization`, `get_optimization_status`, `get_optimization_result`.
- Produces: `normalize_mcp_url(base: str) -> str` (pure, unit-tested) and an async `main()` that connects a FastMCP `Client`, exercises read tools, and (with `--run-optimization`) launches a real cloud optimization and polls to a terminal status. Exits non-zero on any failure.

- [ ] **Step 1: Write the failing unit test**

`tests/unit/test_remote_smoke.py`:

```python
from scripts.validation.remote_smoke import normalize_mcp_url


def test_appends_mcp_path_when_missing():
    assert normalize_mcp_url("https://x.run.app") == "https://x.run.app/mcp/"


def test_preserves_existing_mcp_path():
    assert normalize_mcp_url("https://x.run.app/mcp/") == "https://x.run.app/mcp/"


def test_strips_trailing_slash_before_appending():
    assert normalize_mcp_url("https://x.run.app/") == "https://x.run.app/mcp/"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/unit/test_remote_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError` / cannot import `normalize_mcp_url`.

- [ ] **Step 3: Write `scripts/validation/remote_smoke.py`**

```python
"""Live smoke test against a DEPLOYED Meridian MCP server (streamable-http).

Usage:
  uv run python -m scripts.validation.remote_smoke --url https://<service>.run.app
  uv run python -m scripts.validation.remote_smoke --url ... --run-optimization \
      --model-id <id>

Exits non-zero on any failure. Read-only by default; --run-optimization launches
a REAL cloud optimization job and polls it to completion.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from fastmcp import Client


def normalize_mcp_url(base: str) -> str:
    """Return the streamable-http endpoint URL for a service base URL."""
    base = base.rstrip("/")
    if base.endswith("/mcp"):
        return base + "/"
    return base + "/mcp/"


def _data(result):
    """Extract the structured payload from a FastMCP CallToolResult."""
    return getattr(result, "data", result)


async def _run(url: str, model_id: str | None, run_opt: bool, poll_timeout: int) -> int:
    endpoint = normalize_mcp_url(url)
    print(f"Connecting to {endpoint}")
    async with Client(endpoint) as client:
        tools = [t.name for t in await client.list_tools()]
        print(f"Tools: {sorted(tools)}")
        for required in ("list_models", "get_model_overview", "run_optimization"):
            if required not in tools:
                print(f"FAIL: deployed server missing tool {required}")
                return 1

        models = _data(await client.call_tool("list_models", {}))
        print(f"list_models -> {models}")
        if not models:
            print("FAIL: no models returned by deployed server")
            return 1

        resolved = model_id or (
            models[0]["model_id"] if isinstance(models[0], dict) else models[0]
        )
        overview = _data(
            await client.call_tool("get_model_overview", {"model_id": resolved})
        )
        if not overview or (isinstance(overview, dict) and overview.get("error")):
            print(f"FAIL: get_model_overview errored: {overview}")
            return 1
        print(f"get_model_overview({resolved}) OK")

        if not run_opt:
            print("PASS: read-only smoke test")
            return 0

        started = _data(
            await client.call_tool(
                "run_optimization",
                {
                    "model_id": resolved,
                    "config": {
                        "scenario": {"type": "fixed_budget"},
                        "constraint": {"mode": "global", "pct": 0.3},
                    },
                    "compute_tier": "cloud_cpu",
                    "label": "remote-smoke",
                },
            )
        )
        run_id = started.get("run_id") if isinstance(started, dict) else None
        if not run_id:
            print(f"FAIL: run_optimization did not return a run_id: {started}")
            return 1
        print(f"run_optimization -> run_id={run_id}; polling...")

        waited = 0
        interval = 10
        while waited < poll_timeout:
            status = _data(
                await client.call_tool("get_optimization_status", {"run_id": run_id})
            )
            state = status.get("status") if isinstance(status, dict) else None
            print(f"  [{waited}s] status={state}")
            if state == "completed":
                result = _data(
                    await client.call_tool(
                        "get_optimization_result", {"run_id": run_id}
                    )
                )
                ok = bool(result) and not (
                    isinstance(result, dict) and result.get("error")
                )
                print("PASS: cloud optimization completed" if ok else f"FAIL: {result}")
                return 0 if ok else 1
            if state == "failed":
                print(f"FAIL: optimization failed: {status}")
                return 1
            await asyncio.sleep(interval)
            waited += interval

        print(f"FAIL: optimization did not finish within {poll_timeout}s")
        return 1


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Smoke-test a deployed Meridian MCP server.")
    p.add_argument("--url", default=os.getenv("MERIDIAN_MCP_URL"))
    p.add_argument("--model-id", default=None)
    p.add_argument("--run-optimization", action="store_true")
    p.add_argument("--poll-timeout", type=int, default=1800)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if not args.url:
        print("FAIL: provide --url or set MERIDIAN_MCP_URL")
        return 2
    return asyncio.run(
        _run(args.url, args.model_id, args.run_optimization, args.poll_timeout)
    )


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the unit test to verify it passes**

Run: `uv run pytest tests/unit/test_remote_smoke.py -v`
Expected: 3 passed.

- [ ] **Step 5: Lint**

Run: `uv run ruff check scripts/validation/remote_smoke.py tests/unit/test_remote_smoke.py && uv run ruff format scripts/validation/remote_smoke.py tests/unit/test_remote_smoke.py`
Expected: clean / formatted. Confirm the CLI wires up: `uv run python -m scripts.validation.remote_smoke --help` prints usage.

- [ ] **Step 6: Commit**

```bash
git add scripts/validation/remote_smoke.py tests/unit/test_remote_smoke.py
git commit -m "feat(validation): remote smoke test for a deployed MCP server"
```

---

## Task 10: Runbook + docs (the README update section)

**Files:**
- Create: `deploy/terraform/README.md`
- Modify: `deploy/README.md`
- Modify: `README.md`
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: everything above (variables, outputs, the three Dockerfiles, `remote_smoke.py`).
- Produces: operator-facing documentation. No code depends on this task.

- [ ] **Step 1: Write `deploy/terraform/README.md` (the runbook)**

Include these sections verbatim in intent:

````markdown
# Deploying the Meridian MCP server with Terraform

This provisions the whole hosted stack — Cloud Run **Service** (the MCP server),
Cloud Run **Jobs** (CPU + GPU optimization workers), Artifact Registry, GCS, and
service accounts/IAM — into **one GCP project per client**. The repo ships a
generic config; per-client inputs are **not committed**.

> When editing the `.tf` files, pull current `google` provider syntax from the
> **context7 MCP** (`/hashicorp/terraform-provider-google`) — the GPU fields in
> particular evolve.

## Prerequisites
- `gcloud` + Terraform `>= 1.9` installed; `gcloud auth application-default login`.
- An existing GCP **project** (`project_id`) with billing linked.
- A **GCS bucket for Terraform state** in that project (the bootstrap step below).
- At least one fitted Meridian model uploaded under `gs://<bucket>/<models_prefix>`.

## 1. Bootstrap (manual, once per client)
```bash
gcloud projects create <project_id>              # or use an existing one
gcloud billing projects link <project_id> --billing-account <ACCOUNT_ID>
gcloud storage buckets create gs://<state_bucket> --project <project_id> --location us-central1
```

## 2. Build & push the three images (Cloud Build)
```bash
REPO=us-central1-docker.pkg.dev/<project_id>/meridian
gcloud builds submit --project <project_id> --tag $REPO/server:latest .
gcloud builds submit --project <project_id> --tag $REPO/opt-cpu:latest -f deploy/Dockerfile.worker .
gcloud builds submit --project <project_id> --tag $REPO/opt-gpu:latest -f deploy/Dockerfile.worker.gpu .
```
The `meridian` repo is created by Terraform — for the very first build either run
`terraform apply` once to create it, or pre-create it with
`gcloud artifacts repositories create meridian --repository-format=docker --location=us-central1 --project <project_id>`.
The worker images bundle Meridian + JAX and are multi-GB; allow a long build.

## 3. Configure (uncommitted)
```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars   # fill project_id, bucket, image tags, sizing
cp backend.hcl.example backend.hcl             # the state bucket from step 1
```

## 4. Provision
```bash
terraform init -backend-config=backend.hcl
terraform apply
terraform output service_uri      # the MCP endpoint base (append /mcp/)
```

## 5. Smoke-test the deployed tooling
```bash
uv run python -m scripts.validation.remote_smoke --url "$(terraform output -raw service_uri)"
# end-to-end incl. a real cloud optimization:
uv run python -m scripts.validation.remote_smoke --url "$(terraform output -raw service_uri)" --run-optimization
```
(Requires `allow_unauthenticated = true`, or auth in front of the service.)

## Onboarding a second client
Repeat with a different `project_id`, `gcs_bucket`, image tags, and a **different
`backend.hcl`** (state bucket in that client's project). Same code, different
uncommitted inputs, fully isolated state.

## Teardown (delete everything)
```bash
terraform destroy
gcloud storage rm -r gs://<state_bucket>           # delete TF state bucket
# if the project was throwaway:
gcloud projects delete <project_id>
```
Confirm nothing is left billing: Cloud Run service + jobs, Artifact Registry
images, the models bucket, and the two service accounts.
````

- [ ] **Step 2: Rewrite `deploy/README.md`**

Reframe the file: the manual prerequisites (`gcloud artifacts repositories create`, bucket creation, API enablement) are now **owned by Terraform** — remove them as manual steps. Keep the worker image table (`Dockerfile.worker`, `Dockerfile.worker.gpu`) and the env contract, but state that the **build step is the three `gcloud builds submit` commands** and **provisioning is Terraform** (link to `deploy/terraform/README.md`). Remove any remaining mention of `deploy_jobs.sh`.

- [ ] **Step 3: Add a "Deployment" section to root `README.md`**

Add a short section: the server is hosted on Cloud Run (`streamable-http`), provisioned per client via Terraform; `.env` is **local-dev only** and the deployed env is Terraform-managed; link to `deploy/terraform/README.md`.

- [ ] **Step 4: Add a "Deployment (Terraform)" pointer to `AGENTS.md`**

Under the Configuration area, add a short subsection: build/provision split, Terraform owns resources, runbook at `deploy/terraform/README.md`, and a note to use context7 for provider syntax.

- [ ] **Step 5: Verify links and references**

Run: `grep -rn "deploy/terraform/README.md" README.md AGENTS.md deploy/README.md && grep -rn "deploy_jobs.sh" . --exclude-dir=.git || echo "clean"`
Expected: the runbook is linked from all three docs; no lingering `deploy_jobs.sh` references.

- [ ] **Step 6: Commit**

```bash
git add deploy/terraform/README.md deploy/README.md README.md AGENTS.md
git commit -m "docs(deploy): terraform runbook + reframe deploy docs around build/provision split"
```

---

## Task 11: Live acceptance against `as-dev-anze` — deploy, test, teardown

**Files:** none (operational task; uses uncommitted `terraform.tfvars` + `backend.hcl`).

**Interfaces:**
- Consumes: the full stack (Tasks 1–10), project `as-dev-anze`, region `us-central1`, an existing fitted model in GCS.
- Produces: evidence that `terraform apply` builds the real stack, the deployed tooling works end-to-end (including a real Cloud Run Job execution), and teardown leaves **zero** residual resources.

**This task changes real cloud state and costs money. Run it deliberately; do not background it.**

- [ ] **Step 1: Bootstrap test inputs**

```bash
gcloud config set project as-dev-anze
gcloud storage buckets create gs://as-dev-anze-meridian-tfstate --location us-central1   # if absent
```
Confirm a models bucket with at least one fitted model exists (e.g. `gs://as-dev-anze-meridian-opt/models/...`). If not, upload one.

- [ ] **Step 2: Write throwaway `terraform.tfvars` + `backend.hcl`** (uncommitted)

`deploy/terraform/terraform.tfvars`:
```hcl
project_id = "as-dev-anze"
region     = "us-central1"
gcs_bucket = "as-dev-anze-meridian-opt"   # the bucket holding the test model
create_bucket        = false              # reuse the existing bucket
bucket_force_destroy = false
server_image     = "us-central1-docker.pkg.dev/as-dev-anze/meridian/server:latest"
worker_cpu_image = "us-central1-docker.pkg.dev/as-dev-anze/meridian/opt-cpu:latest"
worker_gpu_image = "us-central1-docker.pkg.dev/as-dev-anze/meridian/opt-gpu:latest"
enable_gpu_job             = false        # set true ONLY if L4 quota is confirmed
optimization_allowed_tiers = "cloud_cpu"
allow_unauthenticated      = true         # required for the remote smoke test
```
`deploy/terraform/backend.hcl`:
```hcl
bucket = "as-dev-anze-meridian-tfstate"
prefix = "meridian/state/as-dev-anze-acceptance"
```

- [ ] **Step 3: Pre-create the registry repo, then build & push images**

```bash
gcloud artifacts repositories create meridian --repository-format=docker --location=us-central1 --project as-dev-anze || true
REPO=us-central1-docker.pkg.dev/as-dev-anze/meridian
gcloud builds submit --project as-dev-anze --tag $REPO/server:latest .
gcloud builds submit --project as-dev-anze --tag $REPO/opt-cpu:latest -f deploy/Dockerfile.worker .
# Skip opt-gpu unless enable_gpu_job = true.
```
Expected: three (or two) successful builds; images visible in Artifact Registry.

- [ ] **Step 4: Provision**

```bash
terraform -chdir=deploy/terraform init -backend-config=backend.hcl
terraform -chdir=deploy/terraform apply
```
Expected: apply succeeds; `terraform -chdir=deploy/terraform output service_uri` prints an `*.run.app` URL. The `meridian` repo already exists from Step 3 — if Terraform errors that it exists, `terraform -chdir=deploy/terraform import` it or set a distinct repo name; note the resolution.

- [ ] **Step 5: Live tooling test against the deployed server**

```bash
URL=$(terraform -chdir=deploy/terraform output -raw service_uri)
uv run python -m scripts.validation.remote_smoke --url "$URL"                       # read-only
uv run python -m scripts.validation.remote_smoke --url "$URL" --run-optimization    # real cloud job
```
Expected: read-only run prints `PASS`. The `--run-optimization` run launches a real `meridian-opt-cpu` execution; confirm in the console (`gcloud run jobs executions list --job meridian-opt-cpu --region us-central1`) and that the script prints `PASS: cloud optimization completed`.

- [ ] **Step 6: Teardown — delete every resource**

```bash
terraform -chdir=deploy/terraform destroy
gcloud storage rm -r gs://as-dev-anze-meridian-tfstate/meridian/state/as-dev-anze-acceptance || true
# Remove test images if desired:
gcloud artifacts docker images delete $REPO/server --delete-tags --quiet || true
gcloud artifacts docker images delete $REPO/opt-cpu --delete-tags --quiet || true
```

- [ ] **Step 7: Confirm zero residual billable resources**

```bash
gcloud run services list --region us-central1
gcloud run jobs list --region us-central1
gcloud iam service-accounts list --project as-dev-anze | grep -E "meridian-(mcp-server|opt-worker)" || echo "SAs gone"
```
Expected: no `meridian-mcp-server` service, no `meridian-opt-*` jobs, the two service accounts gone. The shared models bucket (`create_bucket = false`) is intentionally **not** deleted. Record the results as the acceptance evidence.

- [ ] **Step 8: Remove throwaway inputs**

```bash
rm -f deploy/terraform/terraform.tfvars deploy/terraform/backend.hcl
```
(These were never committed; confirm `git status` is clean.)

---

## Self-Review (completed during planning)

- **Spec coverage:** §2 decisions → Tasks 1–8 + Global Constraints; §3 build/provision split → Task 8 (build) + Tasks 1–7 (provision); §4 env planes → Tasks 5–6 env blocks; §5 layout → Tasks 1–7 file structure; §6 operator workflow → Task 10 runbook; §7 repo changes → Tasks 7 (.gitignore), 8 (deploy_jobs.sh), 10 (docs); §8 validation/teardown → Task 11; §10 README updates → Task 10. All sections mapped.
- **GCS prefix decision** (separate vars, defaults `models/` + `optimizations/`): Task 1 variables + Task 5/6 env. Covered.
- **Placeholders:** none — every `.tf` file and the Python script are shown complete.
- **Type/name consistency:** job short-names `var.cpu_job_name`/`var.gpu_job_name` are the same strings wired into the service's `CLOUD_RUN_JOB_CPU`/`GPU` env (Tasks 5↔6); module outputs consumed by root outputs match (Tasks 5/6↔7); `normalize_mcp_url` defined and tested in the same task (9).
- **Context7:** required-step called out in the header and in every Terraform task's Step 1.
```
