#!/usr/bin/env bash
# Build the worker images via Cloud Build and (re)deploy the Cloud Run Jobs.
#
# Usage:
#   GCS_BUCKET=<bucket> GCS_MODELS_PREFIX=<prefix> bash deploy/deploy_jobs.sh [cpu|gpu|all]
#
# Requires: gcloud auth (ADC), an Artifact Registry docker repo named "meridian"
# in $CLOUD_RUN_REGION, and a GCS bucket holding at least one fitted model.
set -euo pipefail

PROJECT="${CLOUD_RUN_PROJECT:-as-dev-anze}"
REGION="${CLOUD_RUN_REGION:-us-central1}"
BUCKET="${GCS_BUCKET:?set GCS_BUCKET}"
MODELS_PREFIX="${GCS_MODELS_PREFIX:?set GCS_MODELS_PREFIX}"
OPT_PREFIX="${OPTIMIZATION_GCS_PREFIX:-optimizations/}"
REPO="${REGION}-docker.pkg.dev/${PROJECT}/meridian"
TARGET="${1:-all}"

ENV_VARS="PERSISTENCE_BACKEND=gcs,REGISTRY_BACKEND=gcs,GCS_BUCKET=${BUCKET},GCS_MODELS_PREFIX=${MODELS_PREFIX},OPTIMIZATION_GCS_PREFIX=${OPT_PREFIX}"

# Build an image from a given Dockerfile via Cloud Build (large images: bump
# machine type + disk, allow a long timeout).
build_image() {
  local dockerfile="$1" image="$2"
  local cfg
  cfg="$(mktemp)"
  cat > "$cfg" <<EOF
steps:
- name: gcr.io/cloud-builders/docker
  args: ["build","-f","${dockerfile}","-t","${image}","."]
images: ["${image}"]
options:
  machineType: E2_HIGHCPU_8
  diskSizeGb: 100
timeout: 2400s
EOF
  gcloud builds submit --project "$PROJECT" --config "$cfg" .
  rm -f "$cfg"
}

if [[ "$TARGET" == "cpu" || "$TARGET" == "all" ]]; then
  build_image deploy/Dockerfile.worker "${REPO}/opt-cpu:latest"
  gcloud run jobs deploy meridian-opt-cpu --project "$PROJECT" --region "$REGION" \
    --image "${REPO}/opt-cpu:latest" --cpu 4 --memory 16Gi --max-retries 0 \
    --task-timeout 3600 --set-env-vars "$ENV_VARS"
fi

if [[ "$TARGET" == "gpu" || "$TARGET" == "all" ]]; then
  build_image deploy/Dockerfile.worker.gpu "${REPO}/opt-gpu:latest"
  # NVIDIA L4 on Cloud Run Jobs requires --gpu/--gpu-type and a region with L4
  # capacity. GPU jobs cannot scale CPU below the GPU minimum; 4 CPU / 16Gi is safe.
  gcloud run jobs deploy meridian-opt-gpu --project "$PROJECT" --region "$REGION" \
    --image "${REPO}/opt-gpu:latest" --cpu 4 --memory 16Gi --gpu 1 --gpu-type nvidia-l4 \
    --max-retries 0 --task-timeout 3600 --set-env-vars "$ENV_VARS"
fi

echo "Deployed Cloud Run job(s) [${TARGET}] to ${PROJECT}/${REGION}"
