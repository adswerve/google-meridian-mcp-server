# Terraform In-Apply Image Builds + README Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a single `terraform apply` build + push all three container images (via Cloud Build) and provision the whole stack, extend the local live-validation gate with a list→delete optimization flow, and consolidate all deploy/usage docs into a deploy-first root `README.md`.

**Architecture:** A `terraform_data` build resource (one per image, GPU conditional) runs `gcloud builds submit` against the existing `deploy/cloudbuild.yaml`, sitting in the dependency graph between the Artifact Registry repo and the Cloud Run resources. Images are tagged by a content hash of their build inputs, so re-apply rebuilds only on change and Cloud Run picks up new revisions automatically. The three `*_image` operator variables are removed (refs are now computed).

**Tech Stack:** Terraform (`hashicorp/google ~> 7.0`, `terraform_data` built-in), Google Cloud Build, Cloud Run v2, Artifact Registry, Python/pytest (FastMCP validation harness).

## Global Constraints

- Terraform `required_version >= 1.9`; `google` provider `~> 7.0`. No new providers — use the built-in `terraform_data` (NOT `null_resource`).
- `deletion_protection = false` stays on the Cloud Run Service and Jobs.
- Per-client `*.tfvars`, `backend.hcl`, `.terraform/`, `*.tfstate*` are NEVER committed — only `*.example` files are. `.gitignore` already enforces this; do not add real tfvars/backend.hcl.
- `deploy/cloudbuild.yaml`, all three Dockerfiles, and the `.example` files are RETAINED.
- Content-hash tag inputs per image = that image's `Dockerfile` + `pyproject.toml` + `src/**`. `README.md` and `uv.lock` are EXCLUDED from the hash (COPY'd only for packaging metadata / not read by pip).
- The deployed streamable-http endpoint is `/mcp` with NO trailing slash.
- No `Co-Authored-By` trailer on commits (project convention).
- `allow_unauthenticated = true` only for the live tooling test.
- Do NOT change runtime server/worker code or `cloudbuild.yaml`/Dockerfile contents. B3 changes test-harness code only.

---

### Task 1: In-apply image builds (Terraform)

Replace the three out-of-band `*_image` variables with an in-apply build: a `terraform_data.build` per image that runs Cloud Build, content-hash image tags, and rewire the Cloud Run Service + Jobs to the computed refs. All Terraform files change together so `terraform validate` stays green — this is one reviewable unit.

**Files:**
- Create: `deploy/terraform/modules/meridian-stack/builds.tf`
- Modify: `deploy/terraform/modules/meridian-stack/variables.tf:49-63` (remove `server_image`, `worker_cpu_image`, `worker_gpu_image`; add `build_context`)
- Modify: `deploy/terraform/modules/meridian-stack/cloud_run_service.tf:17` and `:88` (image ref + depends_on)
- Modify: `deploy/terraform/modules/meridian-stack/cloud_run_jobs.tf:25,45` and `:69,90` (image refs + depends_on)
- Modify: `deploy/terraform/main.tf:18-22` (drop image inputs, add `build_context`)
- Modify: `deploy/terraform/variables.tf:29-31` (remove the three image vars)
- Modify: `deploy/terraform/terraform.tfvars.example:1-16` (remove image lines + fix header)

**Interfaces:**
- Produces (module locals, consumed within the module only):
  - `local.repo_base` → `"${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repo}"`
  - `local.image_ref` → `map(string)` keyed `"server"|"opt-cpu"|"opt-gpu"`, each `"<repo_base>/<key>:<12-char-hash>"`
  - `local.build_specs` → subset of image specs to build (`opt-gpu` only when `var.enable_gpu_job`)
  - `terraform_data.build` → `for_each = local.build_specs`; `terraform_data.build["server"]`, `["opt-cpu"]`, `["opt-gpu"]`
- Consumes: `var.project_id`, `var.region`, `var.artifact_registry_repo`, `var.enable_gpu_job`, `var.build_context`, `google_artifact_registry_repository.meridian`.

- [ ] **Step 1: Remove the three image variables, add `build_context` (module)**

In `deploy/terraform/modules/meridian-stack/variables.tf`, delete this block (lines 49-63):

```hcl
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
```

Replace it with:

```hcl
# --- Build context ---
variable "build_context" {
  type        = string
  description = "Absolute path to the repo root submitted to Cloud Build and hashed for image tags. Set automatically by the root module; operators never set this."
}
```

- [ ] **Step 2: Create the build resource + image-ref locals**

Create `deploy/terraform/modules/meridian-stack/builds.tf`:

```hcl
# In-apply image builds. Each image is built by Cloud Build (server-side, no
# local Docker) via deploy/cloudbuild.yaml, tagged with a content hash of its
# build inputs so re-apply rebuilds only when those inputs change and Cloud Run
# picks up new revisions automatically.

locals {
  repo_base = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repo}"

  # AR image name => Dockerfile path (relative to build_context).
  image_specs = {
    "server"  = { dockerfile = "Dockerfile" }
    "opt-cpu" = { dockerfile = "deploy/Dockerfile.worker" }
    "opt-gpu" = { dockerfile = "deploy/Dockerfile.worker.gpu" }
  }

  # Build the GPU image only when the GPU job is enabled.
  build_specs = {
    for k, v in local.image_specs : k => v
    if k != "opt-gpu" || var.enable_gpu_job
  }

  # Hash inputs shared by all images: the packaged source + pyproject.
  # README.md and uv.lock are intentionally excluded (COPY'd only for
  # packaging metadata; not read by pip) so doc/lock edits don't rebuild.
  _src_hashes     = [for f in fileset(var.build_context, "src/**") : filesha256("${var.build_context}/${f}")]
  _pyproject_hash = filesha256("${var.build_context}/pyproject.toml")

  image_tag = {
    for k, v in local.image_specs : k => substr(sha256(join("", concat(
      local._src_hashes,
      [local._pyproject_hash, filesha256("${var.build_context}/${v.dockerfile}")],
    ))), 0, 12)
  }

  image_ref = {
    for k, v in local.image_specs : k => "${local.repo_base}/${k}:${local.image_tag[k]}"
  }
}

resource "terraform_data" "build" {
  for_each = local.build_specs

  # Replacing on a changed ref re-runs the create-time build below.
  triggers_replace = local.image_ref[each.key]

  provisioner "local-exec" {
    command = join(" ", [
      "gcloud builds submit ${var.build_context}",
      "--project ${var.project_id}",
      "--config ${var.build_context}/deploy/cloudbuild.yaml",
      "--substitutions=_DOCKERFILE=${each.value.dockerfile},_IMAGE=${local.image_ref[each.key]}",
    ])
  }

  depends_on = [google_artifact_registry_repository.meridian]
}
```

- [ ] **Step 3: Point the Cloud Run Service at the built image**

In `deploy/terraform/modules/meridian-stack/cloud_run_service.tf`, change line 17 from:

```hcl
      image = var.server_image
```
to:
```hcl
      image = local.image_ref["server"]
```

and change the service `depends_on` (line 88) from:

```hcl
  depends_on = [google_project_service.services]
```
to:
```hcl
  depends_on = [google_project_service.services, terraform_data.build]
```

Depend on the whole `terraform_data.build` resource (all image builds), NOT an indexed instance like `terraform_data.build["server"]`. `local.image_ref` is computed from file hashes, so there is no implicit build→deploy edge — `depends_on` is what orders build-before-deploy. Referencing the whole resource also avoids a missing-instance-key error when `opt-gpu` is absent from the `for_each` map (GPU disabled).

- [ ] **Step 4: Point the Cloud Run Jobs at the built images**

In `deploy/terraform/modules/meridian-stack/cloud_run_jobs.tf`:

CPU job — change `image = var.worker_cpu_image` (line 25) to:
```hcl
        image = local.image_ref["opt-cpu"]
```
and the CPU job `depends_on` (line 45) to:
```hcl
  depends_on = [google_project_service.services, terraform_data.build]
```

GPU job — change `image = var.worker_gpu_image` (line 69) to:
```hcl
        image = local.image_ref["opt-gpu"]
```
and the GPU job `depends_on` (line 90) to:
```hcl
  depends_on = [google_project_service.services, terraform_data.build]
```

Both jobs depend on the whole `terraform_data.build` resource (not an indexed instance). `local.image_ref` always has all three keys (it iterates `image_specs`), so `image_ref["opt-gpu"]` is valid even when the GPU image is not built; and depending on the whole resource avoids a missing-instance-key error when `opt-gpu` is absent from the `for_each` map.

- [ ] **Step 5: Update the root module wiring**

In `deploy/terraform/main.tf`, replace lines 18-22:

```hcl
  artifact_registry_repo = var.artifact_registry_repo
  server_image           = var.server_image
  worker_cpu_image       = var.worker_cpu_image
  worker_gpu_image       = var.worker_gpu_image
  enable_gpu_job         = var.enable_gpu_job
```
with:
```hcl
  artifact_registry_repo = var.artifact_registry_repo
  enable_gpu_job         = var.enable_gpu_job

  # Repo root — submitted to Cloud Build and hashed for image tags. path.root is
  # deploy/terraform, so ../.. is the repository root.
  build_context = abspath("${path.root}/../..")
```

In `deploy/terraform/variables.tf`, delete lines 29-31:

```hcl
variable "server_image" { type = string }
variable "worker_cpu_image" { type = string }
variable "worker_gpu_image" { type = string }
```

- [ ] **Step 6: Update the tfvars example**

Replace the entire contents of `deploy/terraform/terraform.tfvars.example` with:

```hcl
# Copy to terraform.tfvars (gitignored) and fill in. Terraform builds and pushes
# the three images during `apply` (via Cloud Build); there are no image tags to set.

project_id = "your-client-project"
region     = "us-central1"

gcs_bucket = "your-client-meridian"
# create_bucket        = true        # set false to reuse an existing bucket
# bucket_force_destroy = false       # true only for throwaway test installs
# gcs_models_prefix       = "models/"
# optimization_gcs_prefix = "optimizations/"

# artifact_registry_repo = "meridian"
# enable_gpu_job = true              # default false; to enable GPU: set true AND add cloud_gpu to optimization_allowed_tiers AND ensure L4 quota in the region

# optimization_allowed_tiers = "cloud_cpu"   # add cloud_gpu when GPU job is enabled
# allow_unauthenticated      = false          # true only for the live tooling test
```

- [ ] **Step 7: Format, init, and validate**

Run: `cd deploy/terraform && terraform fmt -recursive && terraform init -backend=false && terraform validate`
Expected: `terraform fmt` reports no changes (or reformats cleanly), `init` succeeds, and `validate` prints `Success! The configuration is valid.`

If `validate` complains about `terraform_data`, confirm `required_version >= 1.9` in both `versions.tf` files (it already is).

- [ ] **Step 8: Grep for orphaned references**

Run: `grep -rn "server_image\|worker_cpu_image\|worker_gpu_image" deploy/terraform`
Expected: no matches. If any remain, remove them.

- [ ] **Step 9: Commit**

```bash
git add deploy/terraform
git commit -m "feat(terraform): build+push images in-apply via Cloud Build (content-hash tags)"
```

---

### Task 2: Extend the live optimization gate with list → delete

`scripts/validation/runner.py::assert_live_optimization` already drives a real local-tier `run_optimization → status → result → reuse` flow (lines 84-108). Extend it to also exercise `list_optimizations` and `delete_optimization` end-to-end (real subprocess run, real on-disk registry) — the only optimization tools with no live coverage. The registry/service/contract layers already unit-test list/delete with fakes; this adds the genuine end-to-end path.

**Files:**
- Modify: `scripts/validation/runner.py:84-108` (`assert_live_optimization`)

**Interfaces:**
- Consumes (existing MCP tool return shapes, verified in source):
  - `list_optimizations(model_id=?, status=?, limit=?)` → `{"runs": [ {"run_id": str, "status": str, ...}, ... ], "count": int}`
  - `delete_optimization(run_id)` → `{"run_id": str, "deleted": True}`
  - `get_optimization_status(run_id)` after delete → `{"error_code": "optimization_run_not_found", ...}`
- Produces: the extended assertion (same signature `assert_live_optimization(client, model_id, *, overview)`), plus reuses the module-level `call(client, name, args)` and `assert_error(payload, code, label)` helpers already in `runner.py`.

- [ ] **Step 1: Read the current assertion**

Run: `sed -n '84,108p' scripts/validation/runner.py`
Confirm it ends with the reuse check (the `again = ...` block) at line 108.

- [ ] **Step 2: Append the list → delete → verify-gone flow**

In `scripts/validation/runner.py`, at the end of `assert_live_optimization` (immediately after the existing reuse-check block that ends with `assert again["reused"] is True and again["run_id"] == run_id, f"reuse failed: {again}"`), add:

```python
    # list_optimizations must surface this run for the model.
    listing = await call(client, "list_optimizations", {"model_id": model_id})
    assert "error_code" not in listing, f"list_optimizations error: {listing}"
    listed_ids = {r["run_id"] for r in listing["runs"]}
    assert run_id in listed_ids, f"run {run_id} not in list_optimizations: {listed_ids}"
    assert listing["count"] == len(listing["runs"]), (
        f"list count mismatch: {listing['count']} != {len(listing['runs'])}"
    )

    # delete_optimization removes it; a subsequent status lookup must 404 (typed).
    deleted = await call(client, "delete_optimization", {"run_id": run_id})
    assert deleted.get("deleted") is True and deleted.get("run_id") == run_id, (
        f"delete failed: {deleted}"
    )
    gone = await call(client, "get_optimization_status", {"run_id": run_id})
    assert_error(gone, "optimization_run_not_found", f"{model_id}/deleted-run-status")
```

- [ ] **Step 3: Run the full local end-to-end regression gate**

Run: `uv run python -m scripts.validation.live_validate`
Expected: builds fixtures on first run, then prints `PASS` for every matrix label (including `national-revenue/run_optimization[live,local,subprocess]` and `geo-revenue/run_optimization[live,local,subprocess]`, which now also cover list+delete), the cloud gate passes/skips, and the run ends with `LIVE VALIDATION PASSED` (exit 0).

This single command is the Part B2 regression gate — it proves no other tool regressed AND exercises the extended optimization flow (Part B3).

- [ ] **Step 4: Lint + unit-test regression**

Run: `uv run ruff check scripts && uv run ruff format --check scripts`
Expected: both pass. If `format --check` wants changes, run `uv run ruff format scripts` and re-run the check.

Run: `uv run pytest`
Expected: the full suite passes. (This change only touches the validation harness, not `src/`, so pytest is a regression check that nothing else broke.)

- [ ] **Step 5: Commit**

```bash
git add scripts/validation/runner.py
git commit -m "test(validation): cover list/delete optimization in the live local gate"
```

---

### Task 3: Consolidate docs into a deploy-first root README

Delete `deploy/README.md` and `deploy/terraform/README.md`; fold the full operator runbook (rewritten for the one-apply flow), all Terraform variables, and the worker environment contract into a restructured root `README.md` (Deploy → Local development → Reference). Do a correctness pass for stale content.

**Files:**
- Delete: `deploy/README.md`
- Delete: `deploy/terraform/README.md`
- Modify: `README.md` (full restructure)

**Interfaces:**
- Consumes: the removed READMEs' content (operator runbook, worker env contract table, per-execution overrides table), the variable set in `deploy/terraform/variables.tf`, and the existing accurate `README.md` sections (tool surface, response envelope, GCS notes, Docker, local setup).
- Produces: a single `README.md`; no other file links to `deploy/README.md` or `deploy/terraform/README.md`.

- [ ] **Step 1: Delete the two deploy READMEs**

```bash
git rm deploy/README.md deploy/terraform/README.md
```

- [ ] **Step 2: Restructure `README.md` to this top-level outline**

Rewrite `README.md` with these sections in this order:

```
# Google Meridian MCP Server [v0.3.0]
  (2-3 sentence intro — keep the current intro prose)
  ## Tools at a glance      (the full tool-name list, grouped analysis/optimization, one line each)
## Deploy to Google Cloud (Terraform)     ← primary
  ### Architecture
  ### Prerequisites
  ### 1. Bootstrap (once per client)
  ### 2. Configure (uncommitted)
  ### 3. Provision
  ### 4. Smoke-test the deployed server
  ### Onboarding another client
  ### Teardown
## Local development                       ← secondary
  ### Setup / Add a model / Run the server / MCP Inspector
  ### Local optimization tier
  ### Quality checks
  ### Live validation
  ### Docker (local container)
  ### GCS backend notes
## Reference
  ### Tool surface (detailed + response envelope)
  ### Terraform variables
  ### Worker environment contract
  ### Optimization tiers & concepts
```

Move the existing accurate prose into the matching sections: the current "Tool Surface" detail block → Reference ▸ Tool surface; "Local Setup" 1-5 → Local development; "Quality Checks" + "Live validation" → Local development; "GCS Notes" → Local development ▸ GCS backend notes; "Docker" → Local development ▸ Docker; "Budget optimization" concepts → Reference ▸ Optimization tiers & concepts. Keep the `## Tools at a glance` list near the top (all 17 tool names, one short line each).

- [ ] **Step 3: Write the Deploy section with the one-apply flow**

Under `## Deploy to Google Cloud (Terraform)`, use these exact command blocks (the manual build step and targeted-apply/import dance are GONE):

**Architecture** (prose): one `terraform apply` builds and pushes all three images via Cloud Build, then provisions Artifact Registry, GCS, service accounts + IAM, the Cloud Run Service (MCP server), and the Cloud Run Jobs (CPU worker; GPU opt-in). Per-client inputs (`terraform.tfvars`, `backend.hcl`) are never committed. GPU is opt-in (`enable_gpu_job = true` + add `cloud_gpu` to `optimization_allowed_tiers` + L4 quota).

**Prerequisites:**
```
- gcloud + Terraform >= 1.9 installed; `gcloud auth application-default login`.
- An existing GCP project (project_id) with billing linked.
- A GCS bucket for Terraform state (bootstrap below).
- At least one fitted Meridian model uploaded under gs://<bucket>/<models_prefix>.
- Apply runs from a full repo checkout (the Dockerfiles + src/ are the Cloud Build context).
```

**1. Bootstrap:**
```bash
gcloud projects create <project_id>              # or use an existing one
gcloud billing projects link <project_id> --billing-account <ACCOUNT_ID>
gcloud storage buckets create gs://<state_bucket> --project <project_id> --location us-central1
```

**2. Configure:**
```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars   # fill project_id, gcs_bucket, sizing
cp backend.hcl.example backend.hcl             # the state bucket from step 1
```

**3. Provision:**
```bash
terraform init -backend-config=backend.hcl
terraform apply       # builds all 3 images via Cloud Build, then provisions everything
terraform output service_uri   # MCP endpoint base; append /mcp (no trailing slash)
```
Add a note: the first apply is long — up to three ~10-minute Cloud Builds run in parallel. If a build fails mid-apply, re-running `terraform apply` resumes cleanly (it is idempotent).

**4. Smoke-test:**
```bash
uv run python -m scripts.validation.remote_smoke --url "$(terraform output -raw service_uri)"
# end-to-end incl. a real cloud optimization (submit -> poll -> pull result):
uv run python -m scripts.validation.remote_smoke --url "$(terraform output -raw service_uri)" --run-optimization
```
(Requires `allow_unauthenticated = true`, or auth in front of the service.)

**Onboarding another client:** repeat with a different `project_id`, `gcs_bucket`, and a different `backend.hcl` (state bucket in that client's project). Same code, different uncommitted inputs, isolated state.

**Teardown:**
```bash
terraform destroy
gcloud storage rm -r gs://<state_bucket>     # delete TF state bucket
# if the project was throwaway:
gcloud projects delete <project_id>
```

- [ ] **Step 4: Write the Reference ▸ Terraform variables table**

Under `### Terraform variables`, include this table (these are the root variables an operator sets in `terraform.tfvars`):

```markdown
| Variable | Default | Description |
|----------|---------|-------------|
| `project_id` | _(required)_ | Existing GCP project to provision into. |
| `region` | `us-central1` | Region for all regional resources. |
| `gcs_bucket` | _(required)_ | Bucket holding fitted models and optimization run files. |
| `create_bucket` | `true` | Create the bucket here, or reference an existing one. |
| `bucket_force_destroy` | `false` | Allow `destroy` to delete a non-empty bucket (throwaway installs only). |
| `gcs_models_prefix` | `models/` | Key prefix where fitted models live. |
| `optimization_gcs_prefix` | `optimizations/` | Key prefix for optimization run files. |
| `artifact_registry_repo` | `meridian` | Artifact Registry docker repository id. |
| `enable_gpu_job` | `false` | Provision the GPU (L4) worker. Set `true` AND add `cloud_gpu` to `optimization_allowed_tiers` AND ensure L4 quota. |
| `optimization_allowed_tiers` | `cloud_cpu` | Comma-separated tiers the server permits (e.g. `cloud_cpu,cloud_gpu`). |
| `optimization_default_tier` | `auto` | Default tier when a request does not specify one. |
| `allow_unauthenticated` | `false` | Grant `roles/run.invoker` to `allUsers` (live tooling test only; gate behind auth for real clients). |
| `labels` | `{}` | Labels applied to created resources. |
```

Add a one-line note: images are built and tagged automatically (content hash) — there are no image variables. Server/worker sizing and job names have fixed defaults in the module (`modules/meridian-stack/variables.tf`).

- [ ] **Step 5: Write the Reference ▸ Worker environment contract**

Under `### Worker environment contract`, include both tables (moved verbatim from the deleted `deploy/README.md`):

```markdown
Set in the Terraform-managed job definition:

| Variable | Description |
|----------|-------------|
| `PERSISTENCE_BACKEND` | Always `gcs` for cloud workers |
| `REGISTRY_BACKEND` | Always `gcs` for cloud workers |
| `GCS_BUCKET` | Bucket for model storage and optimization run files |
| `GCS_MODELS_PREFIX` | Prefix where fitted models are stored |
| `OPTIMIZATION_GCS_PREFIX` | Prefix for optimization run manifests/state/results |

Injected fresh per execution by the MCP server's `CloudRunJobExecutor`:

| Variable | Description |
|----------|-------------|
| `OPTIMIZATION_RUN_ID` | UUID of the run to execute |
| `MERIDIAN_BACKEND` | JAX backend for the run |
```

- [ ] **Step 6: Correctness pass — fix stale content**

Verify and fix in the new `README.md`:
- The deployed endpoint is `/mcp` (no trailing slash) everywhere it appears.
- No references remain to `deploy/README.md`, `deploy/terraform/README.md`, the manual `gcloud builds submit` step, `deploy_jobs.sh`, or `server_image`/`worker_cpu_image`/`worker_gpu_image`.
- The `## Deployment` and `## Budget optimization` cloud-tier text that pointed at the deleted files now points at in-page anchors or is inlined.
- The title version badge `[v0.3.0]` matches the current package version (check `pyproject.toml` `version`; update the badge if they differ).

Run: `grep -rn "deploy/README\|deploy/terraform/README\|gcloud builds submit\|deploy_jobs\|server_image\|worker_cpu_image\|worker_gpu_image" README.md`
Expected: no matches.

- [ ] **Step 7: Verify no other file links to the deleted READMEs**

Run: `grep -rn "deploy/README.md\|deploy/terraform/README.md" . --include=*.md --include=*.py --include=*.yaml --include=*.toml | grep -v docs/superpowers`
Expected: no matches (the `docs/superpowers/specs` and `plans` history may mention them; those are allowed).

- [ ] **Step 8: Commit**

```bash
git add README.md deploy/README.md deploy/terraform/README.md
git commit -m "docs: consolidate deploy+usage docs into deploy-first root README"
```

---

### Task 4: Live acceptance on `as-dev-anze`

Prove the whole change end-to-end against a real project: one clean `terraform apply` builds all images and provisions the stack, the deployed server serves tools and runs a real optimization whose result is pulled, then `terraform destroy` leaves zero residual. This is the real gate; it consumes real (small) cloud spend and tears down after.

**Files:** none committed. Uses an uncommitted `terraform.tfvars` + `backend.hcl` under `deploy/terraform/` (both gitignored).

**Interfaces:**
- Consumes: Task 1's in-apply build graph, Task 3's runbook commands, `scripts/validation/remote_smoke.py --run-optimization`.
- Produces: recorded evidence in the ledger (apply resource count, image digests in AR, run_id + terminal status + result keys, destroy residual check).

- [ ] **Step 1: Pre-flight — clean slate**

Run: `gcloud run services list --project as-dev-anze --region us-central1` and `gcloud run jobs list --project as-dev-anze --region us-central1` and `gcloud artifacts repositories list --project as-dev-anze --location us-central1`
Expected: confirm no leftover `meridian-*` service/jobs or `meridian` repo from prior runs. If any exist, delete them before proceeding so this is a true from-zero apply.

- [ ] **Step 2: Configure uncommitted inputs**

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars
cp backend.hcl.example backend.hcl
```
Edit `terraform.tfvars`: `project_id = "as-dev-anze"`, `gcs_bucket = "<existing as-dev-anze models bucket>"`, `create_bucket = false`, `allow_unauthenticated = true`. Edit `backend.hcl` to point at the state bucket. (CPU-only: leave `enable_gpu_job` unset/false.)

- [ ] **Step 3: Init and apply from zero**

Run: `terraform init -backend-config=backend.hcl && terraform plan -out=tf.plan`
Review the plan: expect `terraform_data.build["server"]` and `["opt-cpu"]` (no `opt-gpu`), the AR repo, GCS IAM, SAs, the Service, and the CPU Job.

Run: `terraform apply tf.plan`
Expected: apply succeeds; the three-way ordering (repo → builds → Cloud Run) resolves without a targeted apply. Two Cloud Builds run (server, opt-cpu).

- [ ] **Step 4: Confirm images landed**

Run: `gcloud artifacts docker images list us-central1-docker.pkg.dev/as-dev-anze/meridian --include-tags --project as-dev-anze`
Expected: `server` and `opt-cpu` images present, each tagged with a 12-char content hash.

- [ ] **Step 5: Read-only smoke**

Run: `uv run python -m scripts.validation.remote_smoke --url "$(terraform output -raw service_uri)"`
Expected: `PASS: read-only smoke test`; the client lists the full tool set.

- [ ] **Step 6: Full optimization smoke (run + pull result)**

Run: `uv run python -m scripts.validation.remote_smoke --url "$(terraform output -raw service_uri)" --run-optimization`
Expected: a `run_id` is returned, status polls `queued → running → completed`, a real Cloud Run Job execution runs under the worker SA, and `PASS: cloud optimization completed` prints (the result payload is fetched and validated, not just started).

- [ ] **Step 7: Destroy and verify zero residual**

Run: `terraform destroy`
Then: `gcloud run services list ... ; gcloud run jobs list ... ; gcloud artifacts repositories list ...` (same commands as Step 1).
Expected: destroy removes everything it created (Service, Job, AR repo + images, SAs, IAM); zero `meridian-*` residual. The models bucket is preserved (`create_bucket = false`).

- [ ] **Step 8: Record acceptance evidence**

Append to the ledger (`.superpowers/sdd/progress.md`): apply resource count, the two image content-hash tags, the optimization `run_id` + terminal status + result keys, and the destroy residual check. Do NOT commit any `terraform.tfvars`/`backend.hcl`/`tf.plan`/state (all gitignored — verify with `git status --short`, which must show a clean tree).

---

## Notes for the executor

- Tasks 1-3 are independently reviewable and committable. Task 4 commits nothing (it is a live proof) beyond the ledger entry.
- Before each Terraform task, pull current `google`-provider / `terraform_data` syntax from the context7 MCP (`/hashicorp/terraform-provider-google`) if anything looks off — the provider evolves.
- `terraform apply -auto-approve` may be blocked by the environment's auto-mode classifier; use `terraform plan -out=FILE` then `terraform apply FILE` (a saved, reviewed plan), as Task 4 does.
