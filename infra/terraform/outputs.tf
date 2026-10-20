# Identifiers for the deploy workflow's repository variables (#75) and for the
# manual steps in infra/README.md. None of them is secret.

output "project_id" {
  description = "GCP project id."
  value       = var.project_id
}

output "region" {
  description = "Region of the job, scheduler, registry and bucket."
  value       = var.region
}

output "image_repository" {
  description = "Docker repository path; tag images as <this>/newsfeed:<commit sha>."
  value       = "${google_artifact_registry_repository.backend.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.backend.repository_id}"
}

output "job_name" {
  description = "Cloud Run Job that runs the pipeline."
  value       = google_cloud_run_v2_job.pipeline.name
}

output "scheduler_job_name" {
  description = "Cloud Scheduler job that starts the pipeline."
  value       = google_cloud_scheduler_job.pipeline.name
}

output "content_bucket" {
  description = "Bucket holding article content, feed.json and feeds/."
  value       = google_storage_bucket.content.name
}

output "runtime_service_account" {
  description = "Service account the job runs as."
  value       = google_service_account.runtime.email
}

output "deploy_service_account" {
  description = "Service account the deploy workflow impersonates (#75)."
  value       = google_service_account.deploy.email
}
