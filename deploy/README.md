# Deploy: Cloud Run Worker Jobs

This directory contains the container images and deployment script for the
Meridian budget-optimization workers that run as Cloud Run jobs.

## Prerequisites

1. **gcloud CLI** authenticated with Application Default Credentials (ADC):
   ```bash
   gcloud auth application-default login
   ```

2. **Artifact Registry** — a Docker repository named `meridian` must already
   exist in `us-central1` (or the `CLOUD_RUN_REGION` you choose):
   ```bash
   gcloud artifacts repositories create meridian \
     --repository-format=docker \
     --location=us-central1 \
     --project=as-dev-anze
   ```

3. **GCS bucket** — at least one fitted Meridian model must be present under
   `GCS_MODELS_PREFIX` in the bucket you specify. The workers read models from
   the same bucket/prefix at runtime; they do not bake model data into the
   image.

4. **Enabled APIs** — Cloud Build, Cloud Run, Artifact Registry, Cloud Storage.

## Running the deploy script

```bash
export GCS_BUCKET=as-dev-anze-meridian-opt
export GCS_MODELS_PREFIX=models/
# Optionally override defaults:
# export CLOUD_RUN_PROJECT=as-dev-anze
# export CLOUD_RUN_REGION=us-central1
# export OPTIMIZATION_GCS_PREFIX=optimizations/

bash deploy/deploy_jobs.sh
```

The script:
1. Builds the CPU image (`opt-cpu`) via Cloud Build (falls back to local
   `docker build + push` if Cloud Build is unavailable).
2. Deploys the `meridian-opt-cpu` Cloud Run job (4 vCPU, 16 GiB RAM).
3. Builds and pushes the GPU image (`opt-gpu`) locally with `docker build`.
4. Deploys the `meridian-opt-gpu` Cloud Run job (4 vCPU, 16 GiB RAM, 1× NVIDIA L4).

Both jobs are idempotent (create-or-update).

## Container images

| File | Base | JAX install |
|------|------|-------------|
| `Dockerfile.worker` | `python:3.12-slim` | `pip install ".[jax]"` |
| `Dockerfile.worker.gpu` | `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04` | `pip install "." "jax[cuda12]>=0.4"` |

Both images share the same entrypoint:
`python -m google_meridian_mcp_server.execution.worker`

## Environment contract

The following variables are **baked into the job definition** by the deploy
script (sourced from the shell env at deploy time):

| Variable | Description |
|----------|-------------|
| `PERSISTENCE_BACKEND` | Always `gcs` for cloud workers |
| `REGISTRY_BACKEND` | Always `gcs` for cloud workers |
| `GCS_BUCKET` | GCS bucket for model storage and optimization run files |
| `GCS_MODELS_PREFIX` | GCS prefix where fitted Meridian models are stored |
| `OPTIMIZATION_GCS_PREFIX` | GCS prefix for optimization run manifests/state/results |

Default `MERIDIAN_BACKEND=jax` is baked into the image; it can be overridden
per-execution via Cloud Run job overrides (see below).

## Per-execution overrides

The MCP server's `CloudRunJobExecutor` launches each optimization run by
calling `gcloud run jobs run` with per-execution environment overrides. The
two variables injected at execution time are:

| Variable | Description |
|----------|-------------|
| `OPTIMIZATION_RUN_ID` | UUID of the optimization run to execute |
| `MERIDIAN_BACKEND` | JAX backend (`jax` for CPU tier, `jax` + GPU for GPU tier) |

These are NOT baked into the job definition; they are supplied fresh by the
executor for every invocation.

## Notes

- The worker reads the model referenced by `OPTIMIZATION_RUN_ID` from the
  same `GCS_BUCKET`/`GCS_MODELS_PREFIX` as the MCP server, so both must point
  at the same bucket.
- Real image builds run via Cloud Build during deployment; `docker build` is
  the local fallback for the CPU image and the primary build path for the GPU
  image.
- Task 10 performs the live end-to-end smoke test against these deployed jobs.
