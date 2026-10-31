# The pipeline: a Cloud Run Job running `newsfeed run` once per execution, as
# its own least-privilege service account.
#
# The image is set here only on the first apply. After that the deploy
# workflow (#75), or `gcloud run jobs update`, changes it, so Terraform ignores
# the image field; otherwise every plan would roll the job back (ADR-0004).

locals {
  # Settings read by newsfeed.settings (NEWSFEED_*). The default Firestore
  # database is used, so NEWSFEED_FIRESTORE_DATABASE is left unset, and no
  # NEWSFEED_GCS_PREFIX, so the lifecycle rules' prefixes match the keys.
  job_env = {
    NEWSFEED_STORAGE                = "gcp"
    NEWSFEED_GCP_PROJECT            = var.project_id
    NEWSFEED_GCS_BUCKET             = google_storage_bucket.content.name
    NEWSFEED_CONTENT_RETENTION_DAYS = tostring(var.content_retention_days)
  }
}

resource "google_service_account" "runtime" {
  account_id   = "newsfeed-runtime"
  display_name = "newsfeed pipeline (Cloud Run Job runtime)"

  depends_on = [google_project_service.this]
}

# Firestore permissions are project-wide only: there is no per-database role.
resource "google_project_iam_member" "runtime_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_cloud_run_v2_job" "pipeline" {
  name     = var.job_name
  location = var.region

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = google_service_account.runtime.email
      timeout         = "${var.job_timeout_seconds}s"
      # A failed run is not retried: the next hourly run picks up where it
      # left off, and a retry would fetch the same sites twice in an hour.
      max_retries = 0

      containers {
        image = var.image
        args  = ["run"] # the image's entrypoint is `newsfeed`

        resources {
          limits = {
            cpu    = var.job_cpu
            memory = var.job_memory
          }
        }

        dynamic "env" {
          for_each = local.job_env

          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [google_project_service.this]
}
