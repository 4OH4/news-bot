# Cloud Scheduler starts the job on var.schedule (hourly by default) by calling
# the Cloud Run Admin API's jobs.run as its own service account. That account
# needs roles/run.invoker on the job, and nothing else.

resource "google_service_account" "scheduler" {
  account_id   = "newsfeed-scheduler"
  display_name = "newsfeed scheduler (starts the Cloud Run Job)"

  depends_on = [google_project_service.this]
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  location = google_cloud_run_v2_job.pipeline.location
  name     = google_cloud_run_v2_job.pipeline.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "pipeline" {
  name             = "${var.job_name}-schedule"
  description      = "Starts the newsfeed pipeline (Cloud Run Job ${var.job_name})."
  region           = var.region
  schedule         = var.schedule
  time_zone        = var.schedule_time_zone
  attempt_deadline = "180s"

  # A missed start is not retried; the next scheduled run follows within the hour.
  retry_config {
    retry_count = 0
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.pipeline.name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_invoker]
}
