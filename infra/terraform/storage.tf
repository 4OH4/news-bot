# Content bucket: article HTML under articles/, the current feed.json, and
# timestamped feed copies under feeds/ (ADR-0005, ADR-0006).
#
# Private: public access prevention is enforced and access is uniform (IAM
# only). Object versioning stays off, so a lifecycle deletion really removes
# the content; soft delete is off for the same reason (ADR-0005 allows either).
# feed.json gets `Cache-Control: no-cache` from the GcsBlobStore adapter, since
# a bucket has no default Cache-Control setting.

resource "google_storage_bucket" "content" {
  name                        = "${var.project_id}-content"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  soft_delete_policy {
    retention_duration_seconds = 0
  }

  # ADR-0005 decision 1: article content expires after content_retention_days.
  lifecycle_rule {
    condition {
      age            = var.content_retention_days
      matches_prefix = ["articles/"]
    }

    action {
      type = "Delete"
    }
  }

  # ADR-0005 decision 5: timestamped feed copies are kept for 7 days.
  # feed.json itself sits outside both prefixes and never expires.
  lifecycle_rule {
    condition {
      age            = var.feed_copy_retention_days
      matches_prefix = ["feeds/"]
    }

    action {
      type = "Delete"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

# Read and write objects in this bucket only; no bucket or IAM administration.
resource "google_storage_bucket_iam_member" "runtime_objects" {
  bucket = google_storage_bucket.content.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.runtime.email}"
}
