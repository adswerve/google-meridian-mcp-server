# Terraform Deployment for the Meridian MCP Server — Design

**Status:** Approved design (brainstorming output). Implementation plan to follow via writing-plans.
**Date:** 2026-06-30
**Topic:** Codify deployment of the hosted Meridian MCP server + optimization
workers as a reusable, operator-driven Terraform stack, stamped out one GCP
project per client, distributed as part of this open-source repo.

---

## 1. Goal

Replace the current mix of imperative `gcloud`/bash deployment and manual
prerequisites with a single declarative Terraform configuration that provisions
the **entire** per-client stack:

- Cloud Run **Service** — the MCP server (`streamable-http`, port 8080).
- Cloud Run **Jobs** — `meridian-opt-cpu` and `meridian-opt-gpu` (L4) workers.
- Artifact Registry docker repo, GCS bucket + prefixes, service accounts, and
  least-privilege IAM — all today's manual prerequisites, codified.

The repo ships **one generic, reusable configuration**. Per-client specifics
(`terraform.tfvars`, `backend.hcl`, secrets) are **never committed**. An
operator clones the repo, fills two files from committed examples, and runs
`terraform init` + `apply` to install one client. A second client is the same
code with different uncommitted inputs and a different state backend.

## 2. Decisions (resolved during brainstorming)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| IaC tool | **Terraform** | Operator-driven install (no programmatic onboarding), per-client GCP project, OSS distribution, largest ecosystem + Google-maintained GCP modules. Pulumi's Automation API advantage doesn't apply to operator-run installs. |
| Distribution | One generic committed config; per-client inputs uncommitted | OSS project; installer supplies their own `tfvars`/`backend.hcl`. No committed `clients/` folders. |
| Tenancy | One GCP **project per client**, full stack replicated | Strongest isolation; no shared blast radius. |
| State | Remote **GCS backend in the client's own project**, via partial backend config (`-backend-config=backend.hcl`) | Self-contained per client; same committed code targets different state per install. |
| Project creation | **Manual prerequisite** (operator creates project + state bucket first) | Org/billing IAM varies per installer; keep it out of Terraform for OSS portability. |
| Image build | **Option 1** — build stays separate as documented `gcloud builds submit` commands; Terraform consumes an `image_tag` variable | Terraform has no native image-build resource; `local-exec` is an anti-pattern. No required `.sh` artifact. |
| `.env` | Stays **local-dev only**; deployed runtime env is set by Terraform (constants literal, resource refs auto-wired, genuine inputs from tfvars) | No `.env` duplication; auto-wired env can't drift from provisioned resources. |

## 3. Architecture — build plane vs. provision plane

The core principle: **separate building the image from provisioning the
infrastructure.** They change at different rates and have different blast radii.

### 3.1 Build plane (imperative, documented commands — not a maintained `.sh`)

Three images are built with Cloud Build and pushed to Artifact Registry:

| Image | Source | Entrypoint |
|-------|--------|------------|
| `server` | root `Dockerfile` | `python -m google_meridian_mcp_server.server` |
| `opt-cpu` | `deploy/Dockerfile.worker` | `python -m google_meridian_mcp_server.execution.worker` |
| `opt-gpu` | `deploy/Dockerfile.worker.gpu` | same worker, `jax[cuda12]` |

Build is **three documented `gcloud builds submit` commands** in the runbook
(machine type `E2_HIGHCPU_8`, 100 GiB disk, long timeout — images are multi-GB).
Each produces an image tag. **Terraform never builds images**; it consumes a tag
via an `image_tag` variable (or per-image tag variables).

`deploy/deploy_jobs.sh` is **retired as a required artifact**. It may be kept as
an optional convenience wrapper that only builds-and-pushes (no
`gcloud run jobs deploy` — Terraform owns resources now), or deleted. The
implementation plan will choose; default is to delete it and document the three
commands.

### 3.2 Provision plane (Terraform — declarative)

One root module instantiates a `meridian-stack` module that, given a
pre-existing `project_id`, provisions:

1. **API enablement** — Cloud Run, Artifact Registry, Cloud Storage, Cloud Build.
2. **Artifact Registry** — the `meridian` docker repo (region-scoped).
3. **GCS** — the models/optimization bucket + prefixes. Variable chooses
   create-new vs. accept an existing bucket name (existing models may already
   live in a bucket the installer owns).
4. **IAM / service accounts** — least-privilege:
   - `server` SA: object read/write on the bucket, `run.jobs.run` to launch
     workers, `iam.serviceAccountUser` on the worker SA.
   - `worker` SA: object read/write on the bucket.
   Replaces today's reliance on ADC / default compute SA.
5. **Cloud Run Service** — the MCP server, `streamable-http`, port 8080, runs as
   the server SA, env wired to bucket + job names (see §4).
6. **Cloud Run Jobs** — `meridian-opt-cpu` and `meridian-opt-gpu`; GPU job uses
   `template.template.node_selector { accelerator = "nvidia-l4" }`, 4 vCPU /
   16 GiB minimum, `max_retries = 0`, task timeout variable. Tier sizing
   (CPU/mem/timeout) exposed as variables.

**Image tags are the seam between planes:** build produces a tag → Terraform
references it. A deploy is "build → apply".

## 4. Runtime environment: `.env` → Terraform-managed Cloud Run env

`.env` remains the **local development** mechanism (read by `config.py` via
python-dotenv). It is **not** used in Cloud Run and **not** duplicated into
tfvars. The deployed env is composed by Terraform across three categories:

| Category | Examples | Source in Terraform |
|----------|----------|---------------------|
| Fixed constants | `PERSISTENCE_BACKEND=gcs`, `REGISTRY_BACKEND=gcs`, `MCP_TRANSPORT=streamable-http`, `MCP_HOST=0.0.0.0` | Set literally |
| Auto-wired from resources | `CLOUD_RUN_JOB_CPU`, `CLOUD_RUN_JOB_GPU`, `CLOUD_RUN_PROJECT`, `CLOUD_RUN_REGION`, service-account emails | Terraform resource references (cannot drift) |
| Genuine inputs | `GCS_BUCKET`, `GCS_MODELS_PREFIX`, `OPTIMIZATION_GCS_PREFIX`, tier sizing, `OPTIMIZATION_ALLOWED_TIERS` | From `terraform.tfvars` |

`GCS_MODELS_PREFIX` and `OPTIMIZATION_GCS_PREFIX` are **separate Terraform
variables with defaults** — `gcs_models_prefix` defaults to `"models/"` and
`optimization_gcs_prefix` defaults to `"optimizations/"`. They are overridable
in `terraform.tfvars` but can be omitted to take the defaults, keeping a typical
tfvars short. `gcs_bucket` has no default (always supplied). A GCS prefix is a
key path inside the bucket, not a resource — Terraform only ever emits it as an
env value; there is no "create prefix" step.

The server Service and both Jobs each get the subset of env they need. The job
names the server calls (`CLOUD_RUN_JOB_CPU/GPU`) are the **same job resources**
Terraform creates, so the server can never point at a job that doesn't exist.
Per-execution overrides (`OPTIMIZATION_RUN_ID`, `MERIDIAN_BACKEND`) remain
supplied at run time by `CloudRunJobExecutor` — unchanged by this work.

## 5. Repository layout

```
deploy/terraform/
  modules/meridian-stack/        # the whole per-client stack, parameterized (committed)
    apis.tf
    registry.tf
    storage.tf
    iam.tf
    cloud_run_service.tf
    cloud_run_jobs.tf
    variables.tf
    outputs.tf
  main.tf                        # root: provider + calls modules/meridian-stack (committed)
  backend.tf                     # declares empty `backend "gcs" {}` for partial config (committed)
  variables.tf                   # root variables (committed)
  outputs.tf                     # service URL, SA emails, bucket, job names (committed)
  terraform.tfvars.example       # documented placeholders (committed)
  backend.hcl.example            # state-bucket placeholder (committed)
  README.md                      # runbook (committed)
.gitignore                       # add: *.tfvars (except *.example), backend.hcl, .terraform/, *.tfstate*
```

- **One module, N installs.** Onboarding a client edits no tracked file: supply
  `terraform.tfvars` + `backend.hcl`, then `init`/`apply`.
- **Partial backend config** is what lets the same committed code target a
  different client's state: `backend.tf` declares `backend "gcs" {}` empty;
  bucket/prefix come from `terraform init -backend-config=backend.hcl`.

## 6. Operator workflow (per client)

1. **Bootstrap (manual prerequisite, documented; optional helper script):**
   create the GCP project, link billing, create one GCS bucket for Terraform
   state. Outputs: `project_id`, `state_bucket`.
2. **Build images:** three `gcloud builds submit` commands → image tags.
3. **Configure:** copy `terraform.tfvars.example` → `terraform.tfvars`
   (project_id, region, bucket, image tags, tier sizing, env) and
   `backend.hcl.example` → `backend.hcl` (the state bucket).
4. **Provision:** `terraform init -backend-config=backend.hcl` then
   `terraform apply`.
5. **Output:** the server's Cloud Run URL (MCP `streamable-http` endpoint) +
   SA emails + bucket + job names, surfaced as Terraform outputs.

A second client repeats steps 1–4 with different uncommitted inputs and a
different state bucket. No shared state, no collisions.

## 7. Changes to the existing repo

| File | Change |
|------|--------|
| `deploy/deploy_jobs.sh` | Retire as required artifact (delete, or slim to build-and-push only; no `gcloud run jobs deploy`). |
| `deploy/README.md` | Manual prerequisites (registry repo, bucket, API enablement) move *into* Terraform; point to the new runbook + the three build commands. |
| `Dockerfile` (server) | Unchanged as a Dockerfile; now also a built/pushed image in the build step. |
| `.gitignore` | Add Terraform ignores (`*.tfvars` except `*.example`, `backend.hcl`, `.terraform/`, `*.tfstate*`). |
| `deploy/terraform/**` | New, per §5. |
| `AGENTS.md` | Add a short "Deployment (Terraform)" pointer to the runbook. |

## 8. Validation & Acceptance — live deploy, live tooling test, full teardown

This is an acceptance gate executed for real against project **`as-dev-anze`**
(region `us-central1`), not just `terraform plan`.

1. **Live deploy.** Bootstrap a throwaway state bucket in `as-dev-anze`, run the
   three image builds, then `terraform init -backend-config=backend.hcl` +
   `terraform apply`. Apply must succeed and create: APIs enabled, Artifact
   Registry repo, bucket/prefixes (or reuse existing), both SAs + IAM, the
   server Service, both Jobs (incl. GPU — requires L4 capacity/quota in the
   region).
2. **Live tooling test against the *deployed* server.** Using the deployed
   Service URL, connect a FastMCP `Client` over `streamable-http` and exercise
   the tools end-to-end — reusing the `live_validate` approach but pointed at
   the **remote** URL rather than an in-process client. Must include a **real
   optimization run** that launches an actual Cloud Run Job (CPU tier at
   minimum; GPU if quota allows) and reads/writes run files in GCS — proving the
   server→Job→GCS wiring, not just that the Service is up. Requires at least one
   fitted Meridian model present under `GCS_MODELS_PREFIX` in the test bucket.
3. **Full teardown — delete every resource.** Once prod tests pass:
   - `terraform destroy` the stack.
   - Delete the bootstrapped state bucket and any test model objects created
     for the run.
   - If a throwaway project was created for the test, delete the project.
   - Explicit checklist in the runbook confirming **nothing is left billing**
     (Service, Jobs, Artifact Registry images, buckets, SAs).

Acceptance = apply succeeds, remote tooling test passes (including a real Job
execution), and teardown leaves zero residual resources.

## 9. Out of scope (YAGNI)

CI/CD pipelines, CFT project-factory automation of project creation, multi-region,
autoscaling/concurrency tuning, Secret Manager integration, and programmatic
(self-serve) client onboarding. The deliverable is the module + examples +
runbook + retired build script + the live acceptance run.

## 10. README / docs update (delivered with this work)

Documentation changes shipped as part of the implementation, not after:

1. **`deploy/terraform/README.md` (new) — the runbook.** Prerequisites
   (gcloud + ADC, billing, the manual bootstrap step), the three
   `gcloud builds submit` build commands, the configure step (copy the two
   `.example` files, what each variable means), `init -backend-config` +
   `apply`, reading outputs, how to onboard a second client, and the full
   teardown checklist from §8.
2. **`deploy/README.md` (update).** Reframe from "manual prerequisites + bash"
   to "build images here, provision with Terraform there"; remove the manual
   `gcloud artifacts repositories create` / bucket / API-enable steps now owned
   by Terraform; link to the runbook.
3. **Root `README.md` (update).** Add a "Deployment" section summarizing the
   hosted-on-Cloud-Run model and linking to the runbook; clarify that `.env` is
   local-dev only and deployed env is Terraform-managed.
4. **`AGENTS.md` (update).** Short "Deployment (Terraform)" subsection under the
   existing configuration docs pointing at the runbook and the build/provision
   split.
```
