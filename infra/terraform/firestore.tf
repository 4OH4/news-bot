# Firestore (Native mode) for article metadata, source state, feedback and run
# stats. The (default) database is the one the free tier covers.
#
# Protected twice: delete protection on the database, and prevent_destroy so a
# plan that would remove it fails instead (ADR-0004). deletion_policy ABANDON
# means that even if the resource leaves the state, the data stays.

resource "google_firestore_database" "default" {
  name                              = "(default)"
  location_id                       = coalesce(var.firestore_location, var.region)
  type                              = "FIRESTORE_NATIVE"
  delete_protection_state           = "DELETE_PROTECTION_ENABLED"
  deletion_policy                   = "ABANDON"
  point_in_time_recovery_enablement = "POINT_IN_TIME_RECOVERY_DISABLED"
  app_engine_integration_mode       = "DISABLED"

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.this]
}

# Composite indexes the FirestoreRepository queries need (collection scope).
# Without them list_articles, list_feedback and list_run_stats fail with
# FailedPrecondition. The authoritative list is the module docstring of
# backend/src/newsfeed/adapters/firestore_repository.py; keep the two in step.
locals {
  firestore_indexes = {
    articles_by_fetched = {
      collection = "articles"
      fields = [
        { path = "fetched_at", order = "DESCENDING" },
        { path = "id", order = "ASCENDING" },
      ]
    }
    articles_by_status_and_fetched = {
      collection = "articles"
      fields = [
        { path = "extraction.status", order = "ASCENDING" },
        { path = "fetched_at", order = "DESCENDING" },
        { path = "id", order = "ASCENDING" },
      ]
    }
    feedback_by_created = {
      collection = "feedback"
      fields = [
        { path = "created_at", order = "ASCENDING" },
        { path = "id", order = "ASCENDING" },
      ]
    }
    run_stats_by_started = {
      collection = "run_stats"
      fields = [
        { path = "started_at", order = "DESCENDING" },
        { path = "run_id", order = "ASCENDING" },
      ]
    }
  }
}

resource "google_firestore_index" "this" {
  for_each = local.firestore_indexes

  database    = google_firestore_database.default.name
  collection  = each.value.collection
  query_scope = "COLLECTION"

  dynamic "fields" {
    for_each = each.value.fields

    content {
      field_path = fields.value.path
      order      = fields.value.order
    }
  }
}
