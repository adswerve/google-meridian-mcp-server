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
CB="gcloud builds submit --project <project_id> --config deploy/cloudbuild.yaml"
$CB --substitutions=_DOCKERFILE=Dockerfile,_IMAGE=$REPO/server:latest .
$CB --substitutions=_DOCKERFILE=deploy/Dockerfile.worker,_IMAGE=$REPO/opt-cpu:latest .
$CB --substitutions=_DOCKERFILE=deploy/Dockerfile.worker.gpu,_IMAGE=$REPO/opt-gpu:latest .
```
All three images build through `deploy/cloudbuild.yaml`, which runs
`docker build -f <dockerfile>`. (`gcloud builds submit --tag` can only build the
root `./Dockerfile`, so the worker images — which use `deploy/Dockerfile.worker[.gpu]`
— need this `--config` form.) The `meridian` repo is created by Terraform — for the
very first build either run `terraform apply` once to create it (a targeted
`terraform apply -target='module.meridian_stack.google_artifact_registry_repository.meridian'`
is enough), or pre-create it with
`gcloud artifacts repositories create meridian --repository-format=docker --location=us-central1 --project <project_id>`
then `terraform import` it. The worker images bundle Meridian + JAX and are
multi-GB; the config sets a larger machine, 100 GiB disk, and a long timeout.

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
terraform output service_uri      # the MCP endpoint base (append /mcp, no trailing slash)
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
