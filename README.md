# Google Meridian MCP Server [v0.3.0]

FastMCP server exposing a focused set of Google Meridian model-analysis and budget-optimization tools for agents.

This project wraps Google Meridian models behind a small MCP surface so agents can discover available models, inspect model setup, request structured analysis outputs, and submit long-running budget-optimization runs — without needing to understand Meridian's internal APIs directly.

It is designed for both local development and containerized deployment on Google Cloud Run, provisioned per client via Terraform.

## Tools at a glance

**Analysis**

- `list_models` — discover available fitted models.
- `get_model_overview` — metadata: time range, geo scope, channel/input groups, valid output types for other tools.
- `get_training_data` — raw training datasets (KPI, controls, population, spend).
- `get_channel_summary` — ROI, CPIK, mROI, mCPIK, baseline and paid summary metrics per channel.
- `get_contribution` — contribution decomposition by channel.
- `get_adstock_decay` — adstock decay curves and alpha summaries.
- `get_response_curves` — response curves and response curve summaries per channel.
- `get_model_fit` — expected vs actual time series; honors a `geos` filter.
- `get_reach_frequency` — optimal-frequency ROI curves (RF models only).
- `get_channel_data` — per-channel long table across all channel types.
- `get_spend_scenario` — what-if spend change: ROI/mROI or CPIK/mCPIK at new spend level.

**Optimization**

- `run_optimization` — submit a fixed-budget or target-ROAS run (returns immediately with a `run_id`).
- `get_optimization_status` — poll status: `queued → running → completed/failed`.
- `get_optimization_result` — structured result: `summary`, `channel_tables`, `allocation`, `spend_delta`, `outcome_mode`, `response_curves`.
- `list_optimizations` — list runs for a model with optional status filter.
- `delete_optimization` — remove a completed or failed run from the registry.
- `cancel_optimization` — best-effort cancel of a queued or running run.

## Deploy to Google Cloud (Terraform)

### Architecture

A single `terraform apply` builds and pushes all three images via Cloud Build (content-hash tags), then provisions Artifact Registry, GCS, service accounts and IAM, the Cloud Run Service (MCP server), and the Cloud Run Jobs (CPU worker; GPU opt-in). Per-client inputs (`terraform.tfvars`, `backend.hcl`) are never committed. GPU is opt-in (`enable_gpu_job = true` + add `cloud_gpu` to `optimization_allowed_tiers` + L4 quota in the region). The default apply provisions the CPU worker only.

### Prerequisites

- `gcloud` + Terraform `>= 1.9` installed; `gcloud auth application-default login`.
- An existing GCP project (`project_id`) with billing linked.
- A GCS bucket for Terraform state (bootstrap below).
- At least one fitted Meridian model uploaded under `gs://<bucket>/<models_prefix>`.
- Apply runs from a full repo checkout (the Dockerfiles and `src/` are the Cloud Build context).

### 1. Bootstrap (once per client)

```bash
gcloud projects create <project_id>              # or use an existing one
gcloud billing projects link <project_id> --billing-account <ACCOUNT_ID>
gcloud storage buckets create gs://<state_bucket> --project <project_id> --location us-central1
```

### 2. Configure (uncommitted)

```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars   # fill project_id, gcs_bucket, sizing
cp backend.hcl.example backend.hcl             # the state bucket from step 1
```

### 3. Provision

```bash
terraform init -backend-config=backend.hcl
terraform apply       # builds all 3 images via Cloud Build, then provisions everything
terraform output service_uri   # MCP endpoint base; append /mcp (no trailing slash)
```

The first apply is long — the default CPU-only apply runs two Cloud Builds in parallel (the `server` image and the multi-GB `opt-cpu` worker; a third `opt-gpu` build is added when `enable_gpu_job = true`). The worker image can take 10–40 minutes to build (the Cloud Build timeout is 40 min). If a build fails mid-apply, re-running `terraform apply` resumes cleanly (it is idempotent).

> On a brand-new project the Cloud Build service account may lack push access to Artifact Registry. If `apply` fails during `gcloud builds submit` with an Artifact Registry permission error, grant the build service account `roles/artifactregistry.writer` (or run one build manually to surface the exact principal), then re-run `terraform apply`.

### 4. Smoke-test the deployed server

```bash
uv run python -m scripts.validation.remote_smoke --url "$(terraform output -raw service_uri)"
# end-to-end incl. a real cloud optimization (submit -> poll -> pull result):
uv run python -m scripts.validation.remote_smoke --url "$(terraform output -raw service_uri)" --run-optimization
```

(Requires `allow_unauthenticated = true`, or auth in front of the service.)

### Onboarding another client

Repeat with a different `project_id`, `gcs_bucket`, and a different `backend.hcl` (state bucket in that client's project). Same code, different uncommitted inputs, isolated state.

### Teardown

```bash
terraform destroy
gcloud storage rm -r gs://<state_bucket>     # delete TF state bucket
# if the project was throwaway:
gcloud projects delete <project_id>
```

## Local development

### Setup

#### 1. Create a Python environment

Meridian currently targets Python 3.12+.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

#### 2. Install the project

```bash
pip install -e ".[dev]"
```

#### 3. Configure `.env`

Create `.env` in the repository root.

```bash
cp .env.example .env
```

For local filesystem-backed development, a minimal `.env` looks like this:

```dotenv
MCP_TRANSPORT=streamable-http
MCP_HOST=127.0.0.1
MCP_PORT=8000
PERSISTENCE_BACKEND=local
LOCAL_MODELS_ROOT=./models
MODEL_CACHE_ROOT=/tmp/mmm-models
DISCOVERY_TTL_SECONDS=7200
RESULT_CACHE_ENABLED=true
```

`.env` belongs at the project root because the runtime loads it from there explicitly.

### Add a model

Both flat and nested layouts are supported. Nested directories are usually clearer.

```text
models/
├── geo-revenue/
│   └── model.binpb
└── experiment-a/
    └── model.pkl
```

The catalog will expose those examples as model IDs like `geo-revenue` and `experiment-a`.

### Run the server

```bash
python -m google_meridian_mcp_server.server
```

### MCP Inspector

For interactive Inspector testing, the repository includes `fastmcp.json`. The most reliable way to test with your local environment is to start the server yourself and connect the Inspector to `http://localhost:8000/mcp`:

```bash
source .venv/bin/activate
python -m google_meridian_mcp_server.server
# then open MCP Inspector and connect to http://localhost:8000/mcp
```

`fastmcp dev inspector` does not use your activated `.venv` or Conda environment directly. FastMCP launches Inspector servers through a `uv run` subprocess, so inspector-specific dependencies must be declared in `fastmcp.json` or passed with CLI flags such as `--project`, `--with-editable`, and `--with`.

### Local optimization tier

By default all optimization runs execute in a local subprocess (`OPTIMIZATION_ALLOWED_TIERS=local`, `REGISTRY_BACKEND=local`). No extra configuration is needed beyond the defaults in `.env.example`.

To offload heavy runs to Cloud Run Jobs, enable GCS registry and set Cloud Run coordinates in `.env`:

```dotenv
PERSISTENCE_BACKEND=gcs
GCS_BUCKET=<bucket>
REGISTRY_BACKEND=gcs
OPTIMIZATION_GCS_PREFIX=optimizations/
OPTIMIZATION_ALLOWED_TIERS=local,cloud_cpu,cloud_gpu
CLOUD_RUN_PROJECT=<project_id>
CLOUD_RUN_REGION=us-central1
CLOUD_RUN_JOB_CPU=meridian-opt-cpu
CLOUD_RUN_JOB_GPU=meridian-opt-gpu
```

Cloud tiers use a JAX backend (workers run inside a Cloud Run Job execution). The CPU tier has been verified end-to-end. The GPU tier (NVIDIA L4) is deployed and supported; its live smoke is run manually.

### Quality checks

Run tests:

```bash
uv run pytest
```

Run Ruff:

```bash
uv run ruff check src tests scripts
uv run ruff format src tests scripts
```

### Live validation

Build dummy models for every variant and validate every tool live against an in-process MCP client (national vs geo, revenue vs KPI, with adversarial error-path checks):

```bash
uv run python -m scripts.validation.live_validate
```

This generates gitignored fixtures under `models/_validation/` on first run and exits non-zero on any mismatch.

### Docker (local container)

Build locally:

```bash
docker build -t google-meridian-mcp-server .
```

Run locally in Docker:

```bash
docker run --rm -p 8080:8080 --env-file .env -e MCP_HOST=0.0.0.0 google-meridian-mcp-server
```

The container listens on `0.0.0.0` and respects the injected `PORT` environment variable.

### GCS backend notes

When using the GCS backend, authenticate with Application Default Credentials locally:

```bash
gcloud auth application-default login
```

Then set these variables in `.env`:

```dotenv
PERSISTENCE_BACKEND=gcs
GCS_BUCKET=my-project.appspot.com
GCS_MODELS_PREFIX=models/
```

## Reference

### Tool surface

Every tool is annotated as read-only and uses typed parameters with documented validation metadata so the generated schema is stricter and easier for agents to call correctly.

**Response envelope**

Tool responses are canonical JSON payloads. The row-oriented analysis tools return a compact **columnar** envelope: `model_id`, a selector field (`output_type` for analysis tools, or `datasets`/`dataset` for training data), `columns` (the ordered column names), `rows` (a list of positional value lists, one per row), and `row_count`. There is no `data` key and no `result_metadata` block. Measure floats are rounded to 6 significant figures.

Grouped analysis tools return **posterior-only** rows. Prior rows are removed from tool results, and the transport payloads do not include a `distribution` field.

**Per-tool notes**

`get_model_overview` returns the model's time range, geo scope, channel/input groups, flattened data schema, and the supported dataset/output-type values for the other analysis tools.

`get_training_data` accepts one or more dataset keys and returns a single merged result set for the requested selections.

`get_channel_summary` exposes:

- `baseline_summary_metrics`
- `paid_summary_metrics`
- `roi`
- `cpik`
- `marginal_roi`
- `marginal_cpik`

`get_adstock_decay` exposes:

- `adstock_decay`
- `alpha_summary`

`get_response_curves` exposes:

- `response_curves`, which returns numeric curve rows including spend, spend multiplier, metric, and incremental outcome
- `response_curve_summary`, which returns numeric summarized rows keyed by channel, spend, and spend multiplier with `mean`, `ci_lo`, and `ci_hi`

`get_model_fit` returns expected vs actual outcome values alongside baseline and residual series so agents can assess time-series model accuracy. Pass a `geos` filter to fit only selected markets; results are aggregated to one national series (per-geo breakdown is not returned) using Meridian's own `ModelFit` visualizer, so they match the showcase app. An unknown geo raises `missing_model_data`.

`get_reach_frequency` returns optimal-frequency ROI curves for reach & frequency channels; it raises `metric_not_supported` on models that have no RF channels.

`get_channel_data` returns a per-channel long table covering all channel types (paid media, RF, organic media, organic RF, and non-media), useful for inspecting raw spend and impression inputs.

`get_spend_scenario` simulates a what-if change to one channel's spend (a per-time-unit increment, with an optional explicit base spend) and returns the channel's efficiency at the base and new spend levels — ROI/mROI for revenue models, CPIK/mCPIK for KPI-only models.

Note: `roi` and `marginal_roi` output types are only available for revenue models (those with a non-null `revenue_per_kpi`). On KPI-only models, requesting these metrics raises `metric_not_supported`. `cpik` and `marginal_cpik` are valid for all model types.

### Terraform variables

Images are built and tagged automatically (content hash) — there are no image variables. Server/worker sizing and job names have fixed defaults in the module (`modules/meridian-stack/variables.tf`).

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

### Worker environment contract

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

### Optimization tiers & concepts

The optimization tools submit and track long-running Meridian `BudgetOptimizer` runs.

**Tiers**

| Tier | Backend | Use |
|------|---------|-----|
| `local` | Subprocess (default) | Local development; no GCP required. |
| `cloud_cpu` | Cloud Run Job (CPU) | Production runs; requires `REGISTRY_BACKEND=gcs`. |
| `cloud_gpu` | Cloud Run Job (NVIDIA L4) | Large or fast runs; requires `enable_gpu_job = true` and L4 quota. |

The `auto` default tier selects the cheapest allowed tier based on problem size (`OPTIMIZATION_SIZE_THRESHOLDS`).

**Which tier does `auto` pick?**

Selection multiplies `geos × time_periods × channels × posterior_samples` and compares it to `OPTIMIZATION_SIZE_THRESHOLDS` (default `1e7`, `1e8`). Controls, KPI, and spend columns do **not** affect it; `channels` = paid media + reach/frequency channels.

The grid below assumes a typical model — **weekly data over ~2 years (~104 periods)** and **7,000 posterior samples** (7 chains × 1,000 draws):

| channels ↓ \ geos → | 1 (national) | 5 | 10 | 25 | 50 | 100 |
|---|---|---|---|---|---|---|
| **5** | local | cloud_cpu | cloud_cpu | cloud_cpu | cloud_gpu | cloud_gpu |
| **8** | local | cloud_cpu | cloud_cpu | cloud_gpu | cloud_gpu | cloud_gpu |
| **10** | local | cloud_cpu | cloud_cpu | cloud_gpu | cloud_gpu | cloud_gpu |
| **15** | cloud_cpu | cloud_cpu | cloud_gpu | cloud_gpu | cloud_gpu | cloud_gpu |
| **20** | cloud_cpu | cloud_cpu | cloud_gpu | cloud_gpu | cloud_gpu | cloud_gpu |

**Rule of thumb** (this horizon and sampling): `local` when `geos × channels ≲ 14`, `cloud_gpu` when `geos × channels ≳ 137`, and `cloud_cpu` in between — so a national model (1 geo) stays local up to ~13 channels. Other cadences scale the boundaries: 3-year weekly (~156 periods) tips to `cloud_gpu` at `geos × channels ≳ 92`; monthly data keeps far more models on `cloud_cpu`. Longer histories or more posterior draws push runs toward the heavier tiers.

The grid shows the *ideal* pick assuming **all three tiers are enabled**. A deployment that restricts `OPTIMIZATION_ALLOWED_TIERS` (the deployed default is `cloud_cpu` only) makes `auto` fall back to the nearest allowed tier, and an explicit `compute_tier` on `run_optimization` overrides `auto` entirely (subject to the allowed set).

**Validation gates**

```bash
# Local gate (no real GCP project needed — runs full cloud launch/liveness/cancel contract with a fake):
uv run python -m scripts.validation.live_validate

# Real Cloud Run smoke (requires CLOUD_SMOKE=1 and a configured .env with cloud tiers):
CLOUD_SMOKE=1 COMPUTE_TIER=cloud_cpu uv run python -m scripts.validation.cloud_smoke
```
