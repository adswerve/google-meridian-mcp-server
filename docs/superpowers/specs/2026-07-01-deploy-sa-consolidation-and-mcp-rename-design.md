# Deploy Service-Account Consolidation & MCP Rename — Design

**Date:** 2026-07-01
**Status:** Approved (pending spec review)

## Goal

Two independent, small changes to the deployment story:

1. **Service-account consolidation.** Replace today's two unconditionally-created
   runtime service accounts (`meridian-mcp-server` for the Cloud Run service and
   `meridian-opt-worker` for the Cloud Run jobs) with **one** SA that both the
   service and the CPU/GPU jobs run as. By default, use the project's **compute
   engine default service account** and create nothing. Only when the operator
   opts in does Terraform create/adopt a custom SA and bind roles to it.

2. **MCP client name.** Make the server appear as `meridian-mcp` (not `meridian`)
   in MCP clients, and document where the name and its Claude Code pre-approval live.

These are orthogonal; they share a spec only because both are one-file-ish deploy
polish. They can be implemented and reviewed as separate tasks.

---

## Part 1: Service-account consolidation

### Behavior — one input drives everything

A new module input `service_account_id` (root variable threaded into the
`meridian-stack` module), default `""`:

| `service_account_id` | Runtime identity (service **and** jobs) | Creation | IAM bindings |
|---|---|---|---|
| `""` (default) | **Default compute engine SA** (`<project-number>-compute@developer.gserviceaccount.com`) | none | **none** — relies on the default SA's project `Editor` grant |
| e.g. `"meridian-mcp"` | That custom SA (`<id>@<project>.iam.gserviceaccount.com`) | **in-apply gcloud create-if-missing** (adopts an existing SA of that name rather than failing) | `roles/run.developer` (project), `roles/storage.objectAdmin` (bucket), `roles/iam.serviceAccountUser` on itself (actAs) |

`local.manage_sa = var.service_account_id != ""` gates every SA-related resource.

### Mechanism (matches the existing in-apply Cloud Build pattern in `builds.tf`)

`iam.tf` is rewritten. The old `server`/`worker` SAs and their five IAM bindings
are removed. New contents (all gated `count = local.manage_sa ? 1 : 0`):

- **`terraform_data "service_account"`** — a `bash -c` `local-exec` that is
  idempotent:

  ```bash
  set -euo pipefail
  EMAIL="${local.custom_sa_email}"
  gcloud iam service-accounts describe "$EMAIL" --project ${var.project_id} >/dev/null 2>&1 \
    || gcloud iam service-accounts create ${var.service_account_id} \
         --project ${var.project_id} \
         --display-name "Meridian MCP deployment identity"
  sleep 5   # guard SA-creation eventual consistency before the data-source read
  ```

  `triggers_replace = var.service_account_id`; `depends_on = [google_project_service.services]`
  (needs the IAM API). `local.custom_sa_email = "${var.service_account_id}@${var.project_id}.iam.gserviceaccount.com"`.

- **`data "google_service_account" "deploy"`** — reads the SA back for its
  `.email` / `.name`. `depends_on = [terraform_data.service_account]` so the read
  is deferred to **after** creation on the first apply.

- **`google_project_iam_member "deploy_run_developer"`** — `roles/run.developer`,
  member `serviceAccount:${data.google_service_account.deploy[0].email}`.

- **`google_service_account_iam_member "deploy_acts_as_self"`** —
  `roles/iam.serviceAccountUser`, `service_account_id = data.google_service_account.deploy[0].name`,
  member self. (The service, running as this SA, launches jobs that run as this
  same SA; the caller needs actAs on the job's SA.)

- **`google_storage_bucket_iam_member "deploy_bucket"`** — `roles/storage.objectAdmin`
  on `var.gcs_bucket`, member self, `depends_on = [google_storage_bucket.models]`.

### Wiring changes

- **`cloud_run_service.tf`** — `service_account = local.manage_sa ? data.google_service_account.deploy[0].email : null`.
  Null makes Cloud Run auto-use the default compute engine SA.
- **`cloud_run_jobs.tf`** — same expression for both the CPU job and the GPU job
  (replacing `google_service_account.worker.email`).
- **`outputs.tf`** — replace `server_service_account` + `worker_service_account`
  with a single `service_account` output: the custom email when `manage_sa`, else
  `"${data.google_project.this.number}-compute@developer.gserviceaccount.com (default compute engine SA)"`.
- **new `data "google_project" "this"`** (in `apis.tf` or a small `data.tf`) — for
  the project number used in the default-path output.
- **`variables.tf`** (module) + **`main.tf`** (root) + root **`variables.tf`** —
  add and thread `service_account_id` with a description of the default-compute-vs-custom behavior.
- **`terraform.tfvars.example`** — add a commented `# service_account_id = "meridian-mcp"`
  line explaining: blank = default compute engine SA (no SA created, relies on its
  Editor grant); set a name = create/adopt that SA and bind least-privilege roles.

### Assumptions & tradeoffs (accepted)

1. **Default path relies on the compute engine default SA existing.** Standard for
   any project running Cloud Run (GCP auto-creates it), but a project that never
   enabled Compute Engine won't have it. Documented; we do **not** force-enable
   `compute.googleapis.com` (avoids surprise API enablement). The custom path is
   the escape hatch.
2. **Default path has zero IAM bindings** — it leans on the default SA's project
   `Editor` role (which includes `storage.*`, `run.*`, and `iam.serviceAccounts.actAs`).
   If an org has stripped that grant, use the custom path.
3. **Applying to an existing deployment destroys the old `server`/`worker` SAs**
   and switches Cloud Run identities. Fine for dev (`as-dev-anze`); it is a real
   replacement, not additive.

### Verification (no unit tests — Terraform, like the rest of `deploy/`)

- `terraform validate` and `terraform fmt -check`.
- `terraform plan` on the **default** path (`service_account_id` unset): plan shows
  no `google_service_account` / SA-IAM resources; Cloud Run `service_account` is null.
- `terraform plan` on the **custom** path (`service_account_id = "meridian-mcp"`):
  plan shows the `terraform_data` create step, the data source, and the three bindings.
- Live `apply` + `destroy` on `as-dev-anze` for at least one path, capturing the
  `service_account` output and confirming the server serves `/mcp` and an
  optimization run reaches the worker.

---

## Part 2: MCP client name → `meridian-mcp`

### Where the name lives

For a project-scoped `.mcp.json` server, the **key** under `mcpServers` is both the
display name in the client and the exact string Claude Code's `enabledMcpjsonServers`
matches against. The server-side `FastMCP("Google Meridian MCP Server", …)` name in
`server.py:66` is separate and does **not** drive either.

### Changes

- **`.mcp.json`** — rename the `mcpServers` key `"meridian"` → `"meridian-mcp"`
  (URL unchanged).
- **`README.md`** — update any reference to the server registration name to
  `meridian-mcp` (none currently hard-code the `.mcp.json` key, but confirm during
  implementation).
- **Pre-approval (optional, in-repo):** add `"enabledMcpjsonServers": ["meridian-mcp"]`
  to `.claude/settings.json` (project, checked in) so collaborators aren't prompted.
  The value must exactly match the `.mcp.json` key. (Personal alternative:
  `.claude/settings.local.json`, gitignored; user-global: `~/.claude/settings.json`.)
- **Optional alignment (not required):** update the `FastMCP(...)` display name for
  consistency. Left out of the required scope; include only if trivial and desired.

### Verification

- `.mcp.json` is valid JSON with the renamed key.
- If `enabledMcpjsonServers` is added, `.claude/settings.json` stays valid JSON.

---

## Out of scope

- Provider/deployer impersonation identity (Terraform runs under ADC, unchanged).
- Any change to the two-image/three-image Cloud Build flow.
- Renaming Artifact Registry repo, Cloud Run service, or job names (still
  `meridian` / `meridian-mcp-server` / `meridian-opt-*`).
