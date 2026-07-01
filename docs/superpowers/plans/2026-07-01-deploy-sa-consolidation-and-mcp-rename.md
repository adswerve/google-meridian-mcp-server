# Deploy Service-Account Consolidation & MCP Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two always-created Cloud Run service accounts with a single opt-in SA (default = the project's compute engine default SA), and rename the MCP server from `meridian` to `meridian-mcp`.

**Architecture:** A new module input `service_account_id` gates all SA infrastructure via `count`. Empty → Cloud Run service + jobs run as the compute engine default SA, nothing is created, nothing is bound. Non-empty → an in-apply idempotent `gcloud describe || create` step (mirroring `builds.tf`) creates/adopts one SA, a `google_service_account` data source reads it back, and three least-privilege bindings are applied; the service and jobs run as that one SA. The MCP rename is an independent `.mcp.json` key change plus doc/config updates.

**Tech Stack:** Terraform (`>= 1.9`) with `hashicorp/google` provider `7.39.0`; Cloud Run v2; `gcloud` CLI (invoked in-apply); JSON config for `.mcp.json` and `.claude/settings.json`.

## Global Constraints

- Terraform `>= 1.9`; `hashicorp/google` provider pinned at `7.39.0` (do not bump).
- Per-client `terraform.tfvars`, `backend.hcl`, `*.tfstate*`, `.terraform/`, `tf.plan` are NEVER committed — only `*.example` files. Never `git add` any of those.
- No `Co-Authored-By` trailer on commits.
- The `deploy/` tree has NO unit-test tier (consistent with the rest of the repo); verification for HCL is `terraform fmt` + `terraform init -backend=false` + `terraform validate` (offline, no GCP creds). Live `plan`/`apply` against a real project is a separate operator task (Task 4).
- One SA is the identity for BOTH the Cloud Run service and the CPU/GPU jobs.
- Default path (`service_account_id = ""`) creates NO service account and adds NO IAM bindings — it relies on the compute engine default SA's project `Editor` grant.
- Custom path binds exactly three roles: `roles/run.developer` (project), `roles/iam.serviceAccountUser` (on the SA itself), `roles/storage.objectAdmin` (on the bucket).
- `service_account_id` is an account id (short name), NOT an email; the email is derived as `<id>@<project_id>.iam.gserviceaccount.com`.
- The `.mcp.json` `mcpServers` key must become exactly `meridian-mcp` and any `enabledMcpjsonServers` entry must match it exactly.

---

### Task 1: Consolidate to one opt-in service account (Terraform)

This is one atomic change: removing the `server`/`worker` resources breaks every
reference to them at once, so `terraform validate` only passes when the rewrite,
the wiring, and the outputs all land together. Root variable threading is
included so `terraform plan` can exercise both paths from the root module.

**Files:**
- Modify: `deploy/terraform/modules/meridian-stack/iam.tf` (full rewrite)
- Modify: `deploy/terraform/modules/meridian-stack/variables.tf` (add `service_account_id`)
- Modify: `deploy/terraform/modules/meridian-stack/apis.tf` (add `data "google_project" "this"`)
- Modify: `deploy/terraform/modules/meridian-stack/cloud_run_service.tf:10`
- Modify: `deploy/terraform/modules/meridian-stack/cloud_run_jobs.tf:20` and `:59`
- Modify: `deploy/terraform/modules/meridian-stack/outputs.tf:6-12`
- Modify: `deploy/terraform/variables.tf` (add root `service_account_id`)
- Modify: `deploy/terraform/main.tf` (thread `service_account_id`)

**Interfaces:**
- Consumes: existing `var.project_id`, `var.gcs_bucket`, `google_project_service.services`, `google_storage_bucket.models`, `local.image_ref` (from `builds.tf`).
- Produces:
  - `local.manage_sa` (bool) and `local.custom_sa_email` (string) in `iam.tf`.
  - `data.google_service_account.deploy[0].email` / `.name` (custom path only).
  - `data.google_project.this.number` (always).
  - module output `service_account` (string).
  - module + root variable `service_account_id` (string, default `""`).

- [ ] **Step 1: Add the module variable**

In `deploy/terraform/modules/meridian-stack/variables.tf`, insert after the `region` variable block (after line 10, before the `# --- GCS ---` comment):

```hcl
# --- Deployment identity ---
variable "service_account_id" {
  type        = string
  description = "Account id (short name, NOT email) of the single service account the Cloud Run service and jobs run as. Empty (default) makes both run as the project's compute engine default SA and creates/binds nothing. Set a name to create/adopt <id>@<project>.iam.gserviceaccount.com in this project and bind least-privilege roles."
  default     = ""
}
```

- [ ] **Step 2: Add the project data source**

Append to `deploy/terraform/modules/meridian-stack/apis.tf`:

```hcl
# Project number, used to render the compute engine default SA email in outputs.
data "google_project" "this" {
  project_id = var.project_id
}
```

- [ ] **Step 3: Rewrite `iam.tf`**

Replace the ENTIRE contents of `deploy/terraform/modules/meridian-stack/iam.tf` with:

```hcl
# The whole stack (Cloud Run service + CPU/GPU jobs) runs as ONE identity.
#
#   service_account_id = ""   -> the project's compute engine default SA. Create
#                               nothing, bind nothing (relies on its Editor grant).
#   service_account_id = "x"  -> create/adopt x@<project>.iam.gserviceaccount.com
#                               in-apply (idempotent) and bind least-privilege roles.

locals {
  manage_sa       = var.service_account_id != ""
  custom_sa_email = "${var.service_account_id}@${var.project_id}.iam.gserviceaccount.com"
}

# Idempotent create-if-missing, mirroring the in-apply gcloud pattern in builds.tf.
# Adopts an existing SA of the same name instead of failing on re-create.
resource "terraform_data" "service_account" {
  count = local.manage_sa ? 1 : 0

  triggers_replace = var.service_account_id

  provisioner "local-exec" {
    interpreter = ["/bin/bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      if ! gcloud iam service-accounts describe "${local.custom_sa_email}" --project ${var.project_id} >/dev/null 2>&1; then
        gcloud iam service-accounts create ${var.service_account_id} \
          --project ${var.project_id} \
          --display-name "Meridian MCP deployment identity"
        sleep 5
      fi
    EOT
  }

  depends_on = [google_project_service.services]
}

# Read the SA back for its email/name. depends_on defers the read until after the
# create step on the first apply.
data "google_service_account" "deploy" {
  count = local.manage_sa ? 1 : 0

  project    = var.project_id
  account_id = local.custom_sa_email

  depends_on = [terraform_data.service_account]
}

# Launch / read / cancel Cloud Run jobs.
resource "google_project_iam_member" "deploy_run_developer" {
  count = local.manage_sa ? 1 : 0

  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${data.google_service_account.deploy[0].email}"
}

# Run jobs that execute AS this same SA (the service impersonates the job's SA).
resource "google_service_account_iam_member" "deploy_acts_as_self" {
  count = local.manage_sa ? 1 : 0

  service_account_id = data.google_service_account.deploy[0].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${data.google_service_account.deploy[0].email}"
}

# Read / write the bucket (models + run files).
resource "google_storage_bucket_iam_member" "deploy_bucket" {
  count = local.manage_sa ? 1 : 0

  bucket = var.gcs_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${data.google_service_account.deploy[0].email}"

  depends_on = [google_storage_bucket.models]
}
```

- [ ] **Step 4: Point the Cloud Run service at the resolved SA**

In `deploy/terraform/modules/meridian-stack/cloud_run_service.tf`, replace line 10:

```hcl
    service_account = google_service_account.server.email
```

with:

```hcl
    service_account = local.manage_sa ? data.google_service_account.deploy[0].email : null
```

- [ ] **Step 5: Point both jobs at the resolved SA**

In `deploy/terraform/modules/meridian-stack/cloud_run_jobs.tf`, replace BOTH occurrences of:

```hcl
      service_account = google_service_account.worker.email
```

(the CPU job near line 20 and the GPU job near line 59) with:

```hcl
      service_account = local.manage_sa ? data.google_service_account.deploy[0].email : null
```

- [ ] **Step 6: Replace the SA outputs with one**

In `deploy/terraform/modules/meridian-stack/outputs.tf`, replace the two blocks (lines 6-12):

```hcl
output "server_service_account" {
  value = google_service_account.server.email
}

output "worker_service_account" {
  value = google_service_account.worker.email
}
```

with:

```hcl
output "service_account" {
  description = "Identity the Cloud Run service and jobs run as."
  value = local.manage_sa ? data.google_service_account.deploy[0].email : "${data.google_project.this.number}-compute@developer.gserviceaccount.com (default compute engine SA)"
}
```

- [ ] **Step 7: Add the root variable**

In `deploy/terraform/variables.tf`, insert after the `region` block (after line 5, before `variable "gcs_bucket"`):

```hcl
variable "service_account_id" {
  type    = string
  default = ""
}
```

- [ ] **Step 8: Thread it through the module call**

In `deploy/terraform/main.tf`, add inside the `module "meridian_stack"` block — put it right after the `region = var.region` line (line 10):

```hcl
  service_account_id = var.service_account_id
```

- [ ] **Step 9: Format**

Run: `cd deploy/terraform && terraform fmt -recursive`
Expected: prints the paths of any reformatted files (e.g. `modules/meridian-stack/iam.tf`), exit 0. This normalizes the heredoc/alignment.

- [ ] **Step 10: Validate offline**

Run: `cd deploy/terraform && terraform init -backend=false -input=false && terraform validate`
Expected: `Success! The configuration is valid.` (init downloads the pinned provider; `-backend=false` skips the GCS backend so no GCP creds are needed.)

- [ ] **Step 11: Confirm no dangling references to the old SAs**

Run: `cd deploy/terraform && grep -rn 'google_service_account.server\|google_service_account.worker' modules/`
Expected: no output (exit 1). Every reference to the removed resources is gone.

- [ ] **Step 12: Commit**

```bash
cd /Users/anze/Projects/google-meridian-mcp
git add deploy/terraform/modules/meridian-stack/iam.tf \
        deploy/terraform/modules/meridian-stack/variables.tf \
        deploy/terraform/modules/meridian-stack/apis.tf \
        deploy/terraform/modules/meridian-stack/cloud_run_service.tf \
        deploy/terraform/modules/meridian-stack/cloud_run_jobs.tf \
        deploy/terraform/modules/meridian-stack/outputs.tf \
        deploy/terraform/variables.tf \
        deploy/terraform/main.tf
git commit -m "feat(deploy): consolidate to one opt-in service account (default compute engine SA)"
```

---

### Task 2: Document the SA behavior (tfvars example + README + AGENTS)

**Files:**
- Modify: `deploy/terraform/terraform.tfvars.example`
- Modify: `README.md:38` (the Architecture paragraph)
- Modify: `AGENTS.md:69`

**Interfaces:**
- Consumes: the `service_account_id` variable and `service_account` output from Task 1. No code produced.

- [ ] **Step 1: Add the tfvars example line**

In `deploy/terraform/terraform.tfvars.example`, add after the `# enable_gpu_job = true ...` line (line 14):

```hcl

# service_account_id = "meridian-mcp"   # blank = project compute engine default SA (nothing created; relies on its Editor grant). Set a name to create/adopt a dedicated SA and bind least-privilege roles.
```

- [ ] **Step 2: Update the README Architecture paragraph**

In `README.md`, in the paragraph at line 38, replace the clause `provisions Artifact Registry, GCS, service accounts and IAM, the Cloud Run Service` with:

```
provisions Artifact Registry, GCS, the Cloud Run Service
```

Then append this note to the end of that same paragraph (after "provisions the CPU worker only."):

```
**Service account:** the service and jobs run as a single identity. By default (`service_account_id` unset) that is the project's compute engine default service account and Terraform creates/binds nothing — it relies on that SA's project `Editor` grant. Set `service_account_id` to a name (e.g. `meridian-mcp`) and Terraform instead creates or adopts a dedicated SA in the project and grants it least-privilege roles (`run.developer`, `storage.objectAdmin`, and `actAs` on itself). `terraform output service_account` reports which identity is in use. This replaces the previous two-SA (`meridian-mcp-server` + `meridian-opt-worker`) layout.
```

- [ ] **Step 3: Update AGENTS.md deployment note**

In `AGENTS.md` at line 69, replace:

```
The full stack (Cloud Run service + CPU/GPU jobs, Artifact Registry, GCS, IAM) is provisioned
```

with:

```
The full stack (Cloud Run service + CPU/GPU jobs, Artifact Registry, GCS) is provisioned
```

Then, immediately after the sentence ending `then provisions everything.` (line 71), insert:

```
The service and jobs share one identity: the compute engine default SA by default, or a
dedicated SA (created/adopted with least-privilege roles) when `service_account_id` is set.
```

- [ ] **Step 4: Verify AGENTS.md length**

Run: `wc -l /Users/anze/Projects/google-meridian-mcp/AGENTS.md`
Expected: at most 250 lines (project keeps AGENTS.md under 250).

- [ ] **Step 5: Commit**

```bash
cd /Users/anze/Projects/google-meridian-mcp
git add deploy/terraform/terraform.tfvars.example README.md AGENTS.md
git commit -m "docs(deploy): document the consolidated opt-in service account"
```

---

### Task 3: Rename the MCP server to `meridian-mcp`

**Files:**
- Modify: `.mcp.json`
- Create/Modify: `.claude/settings.json` (add `enabledMcpjsonServers`)
- Modify: `README.md` (only if a hard-coded `meridian` server key is referenced)

**Interfaces:**
- Consumes: nothing from earlier tasks (independent). Produces no code.

- [ ] **Step 1: Rename the `.mcp.json` key**

In `.mcp.json`, rename the `mcpServers` key `"meridian"` to `"meridian-mcp"` (leave the `type` and `url` untouched). Result:

```json
{
  "mcpServers": {
    "meridian-mcp": {
      "type": "url",
      "url": "https://meridian-mcp-server-px6atnevbq-uc.a.run.app/mcp"
    }
  }
}
```

- [ ] **Step 2: Verify `.mcp.json` is valid JSON with the new key**

Run: `cd /Users/anze/Projects/google-meridian-mcp && python3 -c "import json; d=json.load(open('.mcp.json')); assert list(d['mcpServers'])==['meridian-mcp'], d; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Pre-approve the server in checked-in Claude settings**

Read `.claude/settings.json` first. Add a top-level `"enabledMcpjsonServers": ["meridian-mcp"]` key (merge into the existing JSON object; do not remove existing keys). If the key already exists, ensure `"meridian-mcp"` is in the array.

- [ ] **Step 4: Verify `.claude/settings.json` is valid JSON**

Run: `cd /Users/anze/Projects/google-meridian-mcp && python3 -c "import json; d=json.load(open('.claude/settings.json')); assert 'meridian-mcp' in d['enabledMcpjsonServers']; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Check the README for a stale server name**

Run: `cd /Users/anze/Projects/google-meridian-mcp && grep -n '"meridian"' README.md`
Expected: no output (exit 1). If a line matches (a `.mcp.json` snippet using the old key), update that key to `meridian-mcp` in the same style as Step 1, then re-run this grep to confirm it is gone.

- [ ] **Step 6: Commit**

```bash
cd /Users/anze/Projects/google-meridian-mcp
git add .mcp.json .claude/settings.json README.md
git commit -m "chore: rename MCP server to meridian-mcp"
```

---

### Task 4: Full live deployment on a real GCP project (MANDATORY acceptance)

This task needs GCP credentials + the real state backend, so it is NOT part of
the offline implementer flow. The controller (or the operator) runs it against
`as-dev-anze` after Tasks 1-3 are merged-ready. This is a REQUIRED full
end-to-end deployment, not a plan-only check: the whole stack is actually
applied, exercised, and torn down. The **custom SA path** is the primary
acceptance target because it exercises everything new (in-apply `gcloud`
create/adopt, the data-source read, and all three bindings); the default path
is confirmed by plan.

Before running, confirm with the operator/user that a live `apply` (and later
`destroy`) against `as-dev-anze` is authorized — it builds images via Cloud
Build and provisions real Cloud Run resources.

**Files:** none (verification only). Any plan/output files go to scratchpad, never the repo.

- [ ] **Step 1: Init with the real backend**

Run: `cd deploy/terraform && terraform init -reconfigure -backend-config=backend.hcl`
Expected: `Terraform has been successfully initialized!`

- [ ] **Step 2: Pre-flight — plan the default path**

Run: `cd deploy/terraform && terraform plan -out /private/tmp/claude-501/-Users-anze-Projects-google-meridian-mcp/0fb920d5-fdd9-46a9-909e-7fc0dbdaf6af/scratchpad/tf-default.plan`
Expected: NO `terraform_data.service_account`, NO `google_service_account`/`data.google_service_account.deploy`, NO `deploy_*` IAM members; the Cloud Run service/job `service_account` resolves to `null`. (Write plan files to scratchpad — `tf.plan` is never committed.)

- [ ] **Step 3: Pre-flight — plan the custom path**

Run: `cd deploy/terraform && terraform plan -var 'service_account_id=meridian-mcp' -out /private/tmp/claude-501/-Users-anze-Projects-google-meridian-mcp/0fb920d5-fdd9-46a9-909e-7fc0dbdaf6af/scratchpad/tf-custom.plan`
Expected: the plan adds `terraform_data.service_account[0]`, reads `data.google_service_account.deploy[0]` (known after apply), and adds the three `deploy_*` bindings; the service/job `service_account` becomes the custom email (known after apply).

- [ ] **Step 4: Full live apply — custom SA path**

Run: `cd deploy/terraform && terraform apply -var 'service_account_id=meridian-mcp' -auto-approve`
Expected: `Apply complete!`. This builds the images via Cloud Build, creates/adopts the `meridian-mcp` SA in-apply, binds the three roles, and provisions Artifact Registry, GCS wiring, the Cloud Run service, and the CPU job. First apply is long (worker image build can take 10–40 min). If a build fails mid-apply, re-run the same command — it is idempotent.

- [ ] **Step 5: Confirm the resolved identity and that bindings landed**

Run: `cd deploy/terraform && terraform output service_account`
Expected: `meridian-mcp@as-dev-anze.iam.gserviceaccount.com`.

Run: `gcloud iam service-accounts describe meridian-mcp@as-dev-anze.iam.gserviceaccount.com --project as-dev-anze`
Expected: the SA exists (exit 0). Confirm the Cloud Run service uses it:

Run: `gcloud run services describe meridian-mcp-server --region us-central1 --project as-dev-anze --format='value(spec.template.spec.serviceAccountName)'`
Expected: `meridian-mcp@as-dev-anze.iam.gserviceaccount.com`.

- [ ] **Step 6: Smoke the live server end-to-end**

Get the endpoint: `cd deploy/terraform && terraform output service_uri` (append `/mcp`, no trailing slash).
Drive one real optimization through the live MCP endpoint using the existing remote smoke path (`scripts/validation/remote_smoke.py` with `--run-optimization` and a novel config so it forces a fresh run — do not reuse a stale completed run). Confirm the run transitions queued → running → completed and that the Cloud Run CPU job logs a `1/1` execution — i.e. the consolidated SA can launch the job, act as itself, and read/write the bucket.
Record the outputs (`service_account`, run id, job execution) as acceptance evidence in the SDD ledger.

- [ ] **Step 7: Idempotence — re-apply adopts, does not recreate**

Run: `cd deploy/terraform && terraform apply -var 'service_account_id=meridian-mcp' -auto-approve`
Expected: `Apply complete!` with no changes to `terraform_data.service_account` (the SA already exists, so the `gcloud describe` short-circuits the create) and no image rebuilds (content hashes unchanged). This proves the "adopt if exists, don't recreate" behavior.

- [ ] **Step 8: Destroy the throwaway install**

Run: `cd deploy/terraform && terraform destroy -var 'service_account_id=meridian-mcp' -auto-approve`
Expected: `Destroy complete!`. This tears down the Cloud Run service/jobs, Artifact Registry, and the three SA IAM bindings.

Run: `gcloud iam service-accounts describe meridian-mcp@as-dev-anze.iam.gserviceaccount.com --project as-dev-anze`
Expected: **the SA still exists** (exit 0), but is now inert — all three role bindings were removed by the destroy. The custom SA is created by an in-apply `gcloud` step inside `terraform_data.service_account` (mirroring the `terraform_data.build` image pattern in `builds.tf`), which has no destroy-time counterpart; `terraform destroy` therefore leaves the SA in place, exactly as it leaves built images in Artifact Registry. This is intended and consistent with the existing pattern — the next apply *adopts* the same SA. Delete it manually with `gcloud iam service-accounts delete meridian-mcp@as-dev-anze.iam.gserviceaccount.com --project as-dev-anze --quiet` if you want it gone. The shared models bucket (`create_bucket = false`) and the project's compute engine default SA are also intentionally NOT deleted. Record the teardown as acceptance evidence.

> **Live acceptance result (2026-07-01, `as-dev-anze`):** All steps passed. Default-path plan = 11 resources, no SA machinery, output `365259031240-compute@developer.gserviceaccount.com (default compute engine SA)`. Custom-path apply = 15 resources; `terraform output service_account` = `meridian-mcp@as-dev-anze.iam.gserviceaccount.com`; Cloud Run service **and** CPU job both run as it. End-to-end smoke: run `geo-revenue-20260701T225509-4fbe48` queued→running→completed with a fresh CPU job execution (`meridian-opt-cpu-688qj`, 1/1 succeeded). Idempotence re-apply: SA machinery only refreshed (adopted, not recreated) — the only churn was the pre-existing `terraform_data.build` image rebuild (out of scope; `builds.tf` unchanged by this plan). Destroy: 15 destroyed, bindings removed, SA left inert (see above) and then manually deleted; bucket + compute-default SA preserved.

---

## Notes for the implementer

- Run `terraform` from `deploy/terraform` (that is the root module; the stack lives in `modules/meridian-stack`).
- Use context7 (`/hashicorp/terraform-provider-google`) if you need to confirm `google_service_account` / `google_project` data-source argument names for provider `7.39.0`.
- Do NOT `git add` `terraform.tfvars`, `backend.hcl`, `*.tfstate*`, `.terraform/`, or any `*.plan` file. Only `*.example` and committed source belong in git.
- Tasks 1-3 are offline and independent enough to review separately; Task 4 is the live gate and requires GCP access.
