#!/usr/bin/env bash
set -euo pipefail

PROJECT="${CLOUD_RUN_PROJECT:-as-dev-anze}"
REGION="${CLOUD_RUN_REGION:-us-central1}"
BUCKET="${GCS_BUCKET:?set GCS_BUCKET}"
MODELS_PREFIX="${GCS_MODELS_PREFIX:?set GCS_MODELS_PREFIX}"
OPT_PREFIX="${OPTIMIZATION_GCS_PREFIX:-optimizations/}"
REPO="${REGION}-docker.pkg.dev/${PROJECT}/meridian"

ENV_VARS="PERSISTENCE_BACKEND=gcs,REGISTRY_BACKEND=gcs,GCS_BUCKET=${BUCKET},GCS_MODELS_PREFIX=${MODELS_PREFIX},OPTIMIZATION_GCS_PREFIX=${OPT_PREFIX}"

# CPU image + job
gcloud builds submit --project "$PROJECT" --tag "${REPO}/opt-cpu:latest" \
  --config /dev/stdin <<EOF || docker build -f deploy/Dockerfile.worker -t "${REPO}/opt-cpu:latest" . && docker push "${REPO}/opt-cpu:latest"
steps:
- name: gcr.io/cloud-builders/docker
  args: ["build","-f","deploy/Dockerfile.worker","-t","${REPO}/opt-cpu:latest","."]
images: ["${REPO}/opt-cpu:latest"]
EOF

gcloud run jobs deploy meridian-opt-cpu --project "$PROJECT" --region "$REGION" \
  --image "${REPO}/opt-cpu:latest" --cpu 4 --memory 16Gi --max-retries 0 --task-timeout 3600 \
  --set-env-vars "$ENV_VARS"

# GPU image + job (NVIDIA L4)
docker build -f deploy/Dockerfile.worker.gpu -t "${REPO}/opt-gpu:latest" .
docker push "${REPO}/opt-gpu:latest"
gcloud run jobs deploy meridian-opt-gpu --project "$PROJECT" --region "$REGION" \
  --image "${REPO}/opt-gpu:latest" --cpu 4 --memory 16Gi --gpu 1 --gpu-type nvidia-l4 \
  --max-retries 0 --task-timeout 3600 --set-env-vars "$ENV_VARS"

echo "Deployed meridian-opt-cpu and meridian-opt-gpu to ${PROJECT}/${REGION}"
