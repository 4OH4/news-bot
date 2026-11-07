# Terraform and provider versions, and the state backend (ADR-0004).
#
# The state bucket is created by ../bootstrap.sh and named at init time, so no
# project-specific value is committed:
#   terraform init -backend-config="bucket=<state bucket>"

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    # Only for the reader app's service: the GA provider has no iap_enabled on
    # google_cloud_run_v2_service (api_service.tf). Kept on the same version
    # as google; drop it once the GA provider has the field.
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
  }

  backend "gcs" {
    prefix = "newsfeed"
  }
}
