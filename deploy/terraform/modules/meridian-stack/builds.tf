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
      "gcloud builds submit '${var.build_context}'",
      "--project ${var.project_id}",
      "--config '${var.build_context}/deploy/cloudbuild.yaml'",
      "--substitutions=_DOCKERFILE=${each.value.dockerfile},_IMAGE=${local.image_ref[each.key]}",
    ])
  }

  depends_on = [google_artifact_registry_repository.meridian]
}
