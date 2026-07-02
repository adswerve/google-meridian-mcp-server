# Terraform In-Apply Image Builds + README Consolidation — Design

**Date:** 2026-07-01
**Status:** Approved (brainstorming)
**Supersedes parts of:** `2026-06-30-terraform-deployment-design.md` (the out-of-band build step)

## Goal

Make a single `terraform apply` provision the entire hosted stack **including
building and pushing all three container images** — eliminating the manual
`gcloud builds submit` step and the targeted-apply/`terraform import`
bootstrap dance. Then consolidate all deployment and usage docs into the root
`README.md`, restructured deploy-first.

This is one cohesive "deployment UX" change: the README must document the new
one-apply flow, so both parts ship together.

---

## Part A — In-apply image builds

### Problem

Three resources have a strict ordering that Terraform cannot currently span in
one apply:

1. `google_artifact_registry_repository.meridian` must exist,
2. the three images must be built and pushed into it,
3. `google_cloud_run_v2_service.server` and `google_cloud_run_v2_job.{cpu,gpu}`
   reference those images.

Today step 2 is manual (`gcloud builds submit` ×3), forcing operators to run a
targeted apply for the repo, build by hand, then a full apply. We collapse this
by inserting a **build resource** between the repo and the Cloud Run resources
in the dependency graph.

### Mechanism: `terraform_data` + `local-exec` → Cloud Build

Use the built-in **`terraform_data`** resource (available since Terraform 1.4;
the module already requires `>= 1.9`) — **no new provider dependency**
(`null_resource`/`hashicorp/null` is avoided). Each build resource runs a
`local-exec` provisioner that calls the *same* server-side Cloud Build already
in use:

```
gcloud builds submit <build_context> \
  --project <project_id> \
  --config <build_context>/deploy/cloudbuild.yaml \
  --substitutions=_DOCKERFILE=<dockerfile>,_IMAGE=<image_ref>
```

`deploy/cloudbuild.yaml` is **retained unchanged** — it is what the build
resource invokes. No local Docker daemon is required; the build runs in Cloud
Build exactly as before.

### Dependency graph

```
google_project_service.services        (run, artifactregistry, cloudbuild, …)
        │
google_artifact_registry_repository.meridian
        │  depends_on
terraform_data.build["server"]                → gcloud builds submit (server)
terraform_data.build["opt-cpu"]               → gcloud builds submit (cpu worker)
terraform_data.build["opt-gpu"]  (gpu only)   → gcloud builds submit (gpu worker)
        │  depends_on
google_cloud_run_v2_service.server   image = local.image_ref["server"]
google_cloud_run_v2_job.cpu          image = local.image_ref["opt-cpu"]
google_cloud_run_v2_job.gpu          image = local.image_ref["opt-gpu"]  (count on enable_gpu_job)
```

- One `terraform_data.build` per image via `for_each` over a 3-entry map. The
  `opt-gpu` entry is included **only when `enable_gpu_job = true`** (filter the
  map with a `for` expression), matching the existing conditional GPU job.
- Each Cloud Run resource sets `image = local.image_ref[<key>]` and adds
  `depends_on = [terraform_data.build[<key>]]` so the build completes before
  the resource is created. The GPU job's `depends_on` references
  `terraform_data.build["opt-gpu"]`, which only exists under the same
  `enable_gpu_job` condition.

### Image map and refs

```hcl
locals {
  repo_base = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repo}"

  # name = AR image name; dockerfile = path relative to build_context
  image_specs = {
    "server"  = { dockerfile = "Dockerfile" }
    "opt-cpu" = { dockerfile = "deploy/Dockerfile.worker" }
    "opt-gpu" = { dockerfile = "deploy/Dockerfile.worker.gpu" }
  }

  # opt-gpu only when the GPU job is enabled
  build_specs = {
    for k, v in local.image_specs : k => v
    if k != "opt-gpu" || var.enable_gpu_job
  }

  image_ref = {
    for k, v in local.image_specs : k => "${local.repo_base}/${k}:${local.image_tag[k]}"
  }
}
```

### Content-hash tags

Each image is tagged with a short SHA over **its build inputs**, so re-apply
rebuilds only when those inputs change, and a changed input yields a new image
ref that Cloud Run picks up as a new revision automatically. No `:latest`
staleness, no unconditional every-apply rebuild.

**Hash inputs per image:** the image's own `Dockerfile` + `pyproject.toml` +
`src/**`.

**Explicitly excluded from the hash:** `README.md` and `uv.lock`. All three
Dockerfiles `COPY README.md` (only to satisfy `pyproject.toml`'s
`readme = "README.md"` packaging metadata — it has zero runtime effect), and
`pip` does not consume `uv.lock`. Excluding them prevents a docs-only or
lockfile-only edit from triggering three ~10-minute image rebuilds.

```hcl
locals {
  src_hashes = [for f in fileset(var.build_context, "src/**") :
                filesha256("${var.build_context}/${f}")]
  pyproject_hash = filesha256("${var.build_context}/pyproject.toml")

  image_tag = {
    for k, v in local.image_specs : k => substr(sha256(join("", concat(
      local.src_hashes,
      [local.pyproject_hash, filesha256("${var.build_context}/${v.dockerfile}")],
    ))), 0, 12)
  }
}
```

The `terraform_data.build` sets `triggers_replace = local.image_ref[each.key]`
(the ref embeds the tag), so a hash change replaces the resource → the
`local-exec` build reruns.

### Variables

**Removed** (no longer operator inputs — now computed from repo + project +
region + hash), in both `modules/meridian-stack/variables.tf` and root
`variables.tf`, and dropped from `terraform.tfvars.example`:

- `server_image`
- `worker_cpu_image`
- `worker_gpu_image`

**Added** (internal; operators never set it):

- `build_context` — absolute path to the repo root submitted to Cloud Build and
  hashed by `fileset`/`filesha256`. The root module passes
  `abspath("${path.root}/../..")` (repo root, from `deploy/terraform`) so it
  resolves regardless of where `terraform` is invoked from.

### Preconditions & failure mode

- At apply time the operator needs `gcloud` authenticated, the
  `cloudbuild.googleapis.com` API enabled (already in the `services`
  `for_each`), and a full repo checkout (the Dockerfiles + `src/` are the build
  context).
- First apply is long: up to 3 × ~10-minute Cloud Builds, run in parallel by
  Terraform.
- If a Cloud Build fails mid-apply, the apply fails with the repo created but
  some images missing; re-running `terraform apply` resumes cleanly
  (idempotent). This is the expected failure mode, not a bug — documented as
  such.

---

## Part B — Live acceptance + local end-to-end regression

### B1. Deployed live acceptance: real optimization + result pull

The deployed live acceptance already exercises a real optimization end to end
via `scripts/validation/remote_smoke.py --run-optimization`, which chains
`run_optimization` → poll `get_optimization_status` to `completed` →
`get_optimization_result`. Make the acceptance criteria **explicit** that:

1. a real cloud optimization run is launched against the deployed server,
2. it is polled to `completed`, and
3. `get_optimization_result` returns a non-empty, non-error payload (the result
   is fetched and validated, not just started).

No code change is required to `remote_smoke.py` for this; it is an
acceptance-criteria clarification. If the existing assertion only checks
truthiness, tighten it to confirm the result payload contains the expected
top-level keys (`summary`, `allocation`).

### B2. Full local end-to-end regression (all tools)

This change touches Terraform, docs, and (B3) the validation harness — none of
the runtime tool code — but the acceptance must still prove **no regression in
any other tool**. Add running the full local end-to-end suite as an acceptance
gate:

```bash
uv run python -m scripts.validation.live_validate
```

This builds the dummy-model fixtures and runs the whole tool matrix (national
vs geo, revenue vs KPI, adversarial error paths) plus the existing cloud-tier
optimization gate, exiting non-zero on any mismatch. It must pass clean.

### B3. Add a simple local-tier optimization flow to the end-to-end

`live_validate` currently covers optimization only through the **cloud-tier**
gate (`assert_cloud_live_optimization`, TF + JAX cross-backend, which launches a
real worker locally). Add a **simple `local`-tier** `run_optimization`
happy-path to the suite — the default tier an operator hits first and the one
with no coverage in the end-to-end today:

1. Submit `run_optimization` on a fixtures model with `compute_tier=local` and a
   minimal fixed-budget config.
2. Poll `get_optimization_status` to `completed` (bounded timeout).
3. Fetch `get_optimization_result` and assert the payload is non-empty and
   contains the expected top-level keys (`summary`, `allocation`).

Implement as a new assertion (e.g. `assert_local_live_optimization`) invoked
from `live_validate` alongside the existing cloud gate, reusing the fixtures and
in-process MCP/service wiring already present. It must be a real submit → poll →
result flow (not a fake), running in the default local subprocess tier.

---

## Part C — README consolidation & restructure

### Files removed

- `deploy/README.md` — deleted; content folded into root `README.md`.
- `deploy/terraform/README.md` — deleted; content folded into root `README.md`.

All cross-links in the root README that point at those two files are removed or
replaced with in-page anchors. `deploy/cloudbuild.yaml`, the Dockerfiles, and
the `.example` files are **retained**.

### New structure (deploy-first; Approach 1)

```
# Google Meridian MCP Server [vX.Y.Z]
  2-3 sentence intro (what it is)
  Tools at a glance — the full list of all tool names, one short line each,
    grouped (analysis · optimization). Detail lives in Reference below.

## Deploy to Google Cloud (Terraform)         ← primary path
  Architecture (what gets provisioned; one `terraform apply` builds + deploys)
  Prerequisites (gcloud auth, Terraform >= 1.9, existing project + billing,
    a GCS state bucket, at least one fitted model in the models bucket)
  1. Bootstrap (project + billing + state bucket — manual, once per client)
  2. Configure (cp *.example → terraform.tfvars + backend.hcl; uncommitted)
  3. Provision (terraform init -backend-config=backend.hcl && terraform apply —
     note: builds all three images via Cloud Build, then provisions everything;
     first apply is long)
  4. Smoke-test the deployed server (remote_smoke.py, incl. a real optimization
     run + result pull)
  Onboarding another client (different project_id / bucket / backend.hcl)
  Teardown (terraform destroy + delete state bucket)

## Local development                          ← secondary path
  Setup (venv, pip install -e ".[dev]", .env)
  Add a model
  Run the server
  MCP Inspector
  Local optimization tier
  Quality checks (pytest, ruff)
  Live validation (scripts.validation.live_validate)
  Docker (local container run)
  GCS backend notes

## Reference
  Tool surface (detailed per-tool docs + columnar response envelope)
  Terraform variables (full table: name · description · default)
  Worker environment contract (env baked into the jobs; per-execution overrides)
  Optimization tiers & concepts (local / cloud_cpu / cloud_gpu)
```

### Content mapping

- The **operator runbook** (bootstrap → configure → provision → smoke-test →
  onboard → teardown) is rewritten to the **new one-apply flow**: the "Step 2
  build & push" section and the targeted-apply/`terraform import` bootstrap are
  **deleted** (Terraform now builds). Only `terraform init` + `terraform apply`
  remain.
- **All Terraform variable definitions** move into a single table in Reference:
  every variable in `modules/meridian-stack/variables.tf` / root
  `variables.tf` with its description and default (the three `*_image`
  variables are gone; `build_context` is internal and noted as such or
  omitted from the operator-facing table).
- The **worker environment contract** and **per-execution overrides** tables
  (currently in `deploy/README.md`) move into Reference verbatim.
- The **GPU opt-in callout**, `/mcp` (no trailing slash) endpoint note, and the
  image table move into the relevant Reference / Deploy subsections.
- Existing accurate sections (Tool Surface details, response envelope, GCS
  notes, Docker, local setup) are **preserved** and slotted into the new
  structure — reworded only where they referenced the removed files or the old
  build flow.

### Correctness pass

While restructuring, verify the README against current behavior and fix stale
content, including at least:

- `run_optimization` mentions `compute_tier`/tiers consistently with the actual
  tool params.
- The deployed endpoint is `/mcp` (no trailing slash) everywhere.
- No remaining references to `deploy/README.md`, `deploy/terraform/README.md`,
  the manual build commands, `deploy_jobs.sh`, or the `*_image` tfvars.
- Version badge in the title matches the current package version.

---

## Testing / acceptance

There is no Terraform unit-test harness in this repo (tests are pytest for the
server). Verification is:

1. `terraform fmt -check` + `terraform validate` — static.
2. `terraform plan` against `as-dev-anze` — confirms the graph resolves and
   image refs compute without running a build.
3. **Full local end-to-end regression** (Part B2/B3):
   `uv run python -m scripts.validation.live_validate` passes clean — the whole
   tool matrix plus the new local-tier optimization flow and the existing
   cloud-tier gate — proving no other tool regressed.
4. `uv run pytest` + `uv run ruff check` / `ruff format --check` pass (the new
   assertion and any harness change are covered/lint-clean).
5. **Deployed live acceptance** (the real gate): clean `terraform apply` from
   zero → assert all three (or two, CPU-only) images land in Artifact Registry
   and Cloud Run comes up healthy → `remote_smoke.py --run-optimization`
   launches a real optimization, polls to `completed`, and **pulls a valid
   result** → `terraform destroy` leaves zero residual (models bucket preserved
   per `create_bucket`/`bucket_force_destroy`).
6. README: manual read-through of the restructured file; every command block
   copy-paste runnable; no dead links or stale flags.

## Out of scope

- The `kreuzwerker/docker` local-build provider (rejected: requires local
  Docker + multi-GB local builds; breaks the apply-only operator story).
- Digest-pinning / drift tracking on image contents beyond the content-hash tag.
- Any change to `cloudbuild.yaml`, the Dockerfiles' contents, or the runtime
  server/worker code.
- CI-prebuilt-image escape hatch (the `*_image` variables) — removed per the
  "fully replace the manual build path" decision.
