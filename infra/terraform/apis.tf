# APIs Terraform needs beyond the ones bootstrap.sh enables (Cloud Storage,
# Service Usage, Cloud Resource Manager). disable_on_destroy is false so that
# removing a resource never switches an API off under something still using it.

locals {
  services = toset([
    "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudscheduler.googleapis.com",
    "firestore.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com", # the deploy workflow impersonates its account
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "sts.googleapis.com", # swaps GitHub's token for a Google one (wif.tf)
  ])
}

resource "google_project_service" "this" {
  for_each = local.services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
