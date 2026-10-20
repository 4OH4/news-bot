# Docker repository for the backend image. The deploy workflow (#75) pushes an
# image tagged with each commit SHA to it.
#
# Cleanup keeps storage (and cost) flat: images older than 30 days are deleted,
# except the 10 most recent, which always include the one the job runs.

resource "google_artifact_registry_repository" "backend" {
  location      = var.region
  repository_id = "newsfeed"
  description   = "newsfeed backend images, pushed by the deploy workflow."
  format        = "DOCKER"

  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"

    most_recent_versions {
      keep_count = 10
    }
  }

  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"

    condition {
      older_than = "2592000s" # 30 days
    }
  }

  depends_on = [google_project_service.this]
}
