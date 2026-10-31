# Secret Manager. The pipeline needs no secrets yet, so var.secret_ids is empty
# and this creates nothing beyond enabling the API (apis.tf).
#
# Terraform creates only the secret containers and who may read them, never
# their values, which would be stored in plain text in the state (ADR-0004
# decision 7). Add a value with:
#   gcloud secrets versions add <id> --data-file=-
# and expose it to the job with an env block whose value_source names the secret.

resource "google_secret_manager_secret" "this" {
  for_each = var.secret_ids

  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.this]
}

resource "google_secret_manager_secret_iam_member" "runtime_access" {
  for_each = google_secret_manager_secret.this

  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}
