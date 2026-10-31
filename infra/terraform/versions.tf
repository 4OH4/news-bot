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
  }

  backend "gcs" {
    prefix = "newsfeed"
  }
}
