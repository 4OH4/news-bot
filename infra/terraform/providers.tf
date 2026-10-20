# One provider for the whole root module.
#
# With user credentials (Application Default Credentials), the Cloud Billing
# Budgets API and Service Usage need a quota project. billing_project plus
# user_project_override send every request with this project as the quota
# project, so the budget applies without extra configuration (ADR-0004,
# Consequences). infra/README.md also sets the ADC quota project, which covers
# the few calls made before this provider block is in effect.

provider "google" {
  project               = var.project_id
  region                = var.region
  billing_project       = var.project_id
  user_project_override = true
}

data "google_project" "this" {
  project_id = var.project_id
}
