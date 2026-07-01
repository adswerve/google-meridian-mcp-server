# Deploy: Cloud Run Worker Images

This directory contains the container images for the Meridian server and the
budget-optimization workers that run as Cloud Run Jobs.

Infrastructure provisioning — Artifact Registry, GCS bucket, Cloud Run service
and jobs, IAM — is fully managed by Terraform. See
[`deploy/terraform/README.md`](terraform/README.md) for the full operator runbook
(bootstrap, build, configure, provision, smoke-test).

## Build & push images (Cloud Build)

After Terraform has provisioned the Artifact Registry repository:

```bash
REPO=us-central1-docker.pkg.dev/<project_id>/meridian
gcloud builds submit --project <project_id> --tag $REPO/server:latest .
gcloud builds submit --project <project_id> --tag $REPO/opt-cpu:latest -f deploy/Dockerfile.worker .
gcloud builds submit --project <project_id> --tag $REPO/opt-gpu:latest -f deploy/Dockerfile.worker.gpu .
```

The worker images bundle Meridian + JAX and are multi-GB; allow a long build.
The GPU worker requires L4 quota in the deployment region.

## Container images

| File | Base | JAX install |
|------|------|-------------|
| `Dockerfile.worker` | `python:3.12-slim` | `pip install ".[jax]"` |
| `Dockerfile.worker.gpu` | `python:3.12-slim` | `pip install "." "jax[cuda12]>=0.4"` |

The GPU image uses the same slim Python base as the CPU image: `jax[cuda12]`
ships self-contained CUDA runtime wheels, and Cloud Run's L4 runtime provides
the GPU driver, so no `nvidia/cuda` base image is required.

Both images share the same entrypoint:
`python -m google_meridian_mcp_server.execution.worker`

## Environment contract

The following variables are **set in the Terraform-managed job definition**:

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
- Both images are built via Cloud Build during deployment (no local Docker
  required).
