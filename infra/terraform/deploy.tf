# The deploy service account that GitHub Actions impersonates (#75). wif.tf holds
# the Workload Identity Federation pool and provider, and the binding that lets
# the deploy workflow on the main branch act as this account.
#
# It may only push images to the one repository and update the one job and the
# one service. To set an image it must also act as that resource's runtime
# account, because an update re-submits its service account; hence
# serviceAccountUser on those two accounts (and on no other).

resource "google_service_account" "deploy" {
  account_id   = "newsfeed-deploy"
  display_name = "newsfeed deploy (GitHub Actions via WIF)"

  depends_on = [google_project_service.this]
}

resource "google_artifact_registry_repository_iam_member" "deploy_push" {
  location   = google_artifact_registry_repository.backend.location
  repository = google_artifact_registry_repository.backend.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_cloud_run_v2_job_iam_member" "deploy_update" {
  location = google_cloud_run_v2_job.pipeline.location
  name     = google_cloud_run_v2_job.pipeline.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_service_account_iam_member" "deploy_acts_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deploy.email}"
}

# The reader app's service (api_service.tf): update its image, nothing else.
# run.developer can't set IAM policy, so it can't add an invoker to the service.
resource "google_cloud_run_v2_service_iam_member" "deploy_update_api" {
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${google_service_account.deploy.email}"
}

resource "google_service_account_iam_member" "deploy_acts_as_api" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deploy.email}"
}
