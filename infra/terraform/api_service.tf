# The reader app's service: a Cloud Run service running `newsfeed serve`
# (the built client and its API, on one origin), as its own service account,
# behind Identity-Aware Proxy (IAP) (docs/phases/phase-3.md, ADR-0010, #196).
#
# IAP is turned on directly on the service (iap_enabled), with no load
# balancer. Google refuses every request from an account without
# roles/iap.httpsResourceAccessor before it reaches the container, so a flood
# from strangers starts no instance and isn't billed (#196, spike A). The API
# repeats the owner check on every route (#175), in case IAP is ever off.
#
# Deliberately NOT in Terraform:
# - google_iap_settings and any OAuth client (google_iap_client,
#   google_iap_brand). The OAuth client secret would be stored in Terraform
#   state as plain text. The owner sets IAP's OAuth settings once in the
#   console (infra/README.md, "The reader app behind IAP"; #179). Never move
#   them here.
# - The owner's roles/iap.httpsResourceAccessor grant. #179 makes it in the
#   same console dialog as the OAuth settings. If it is ever wanted here, it is
#   a google_iap_web_cloud_run_service_iam_member, and holds no secret.
#
# There is no allUsers or allAuthenticatedUsers grant, and there must never be
# one: with IAP the only invoker is IAP's own service agent (below).
#
# The image is set here only on the first apply; the deploy workflow (#178)
# changes it afterwards, so Terraform ignores it, as for the job (ADR-0004).

locals {
  # The audience of the signed header IAP adds (x-goog-iap-jwt-assertion) for
  # a Cloud Run service; the API checks the JWT's aud against it (#175, #196 §5).
  api_iap_audience = "/projects/${data.google_project.this.number}/locations/${var.region}/services/${var.api_service_name}"

  # IAP's service agent, which Google creates for the project. It forwards the
  # requests IAP lets through, so it alone needs to invoke the service.
  iap_service_agent = "service-${data.google_project.this.number}@gcp-sa-iap.iam.gserviceaccount.com"

  # Settings read by newsfeed.settings (NEWSFEED_*), as for the job, plus the
  # API's own check (#175). NEWSFEED_OWNER_EMAIL is set in its own env block
  # below: var.owner_email is sensitive, and inside this map it would hide
  # every value here in the plan.
  api_env = {
    NEWSFEED_STORAGE                = "gcp"
    NEWSFEED_GCP_PROJECT            = var.project_id
    NEWSFEED_GCS_BUCKET             = google_storage_bucket.content.name
    NEWSFEED_CONTENT_RETENTION_DAYS = tostring(var.content_retention_days)
    NEWSFEED_API_AUTH               = "iap"
    NEWSFEED_IAP_AUDIENCE           = local.api_iap_audience
  }
}

resource "google_service_account" "api" {
  account_id   = "newsfeed-api"
  display_name = "newsfeed reader API (Cloud Run service runtime)"

  depends_on = [google_project_service.this]
}

# Feedback is written to Firestore, whose permissions are project-wide only:
# there is no per-database or per-collection role.
resource "google_project_iam_member" "api_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.api.email}"
}

# Read feed.json and articles/, and write saved/, in the content bucket only;
# no bucket or IAM administration.
resource "google_storage_bucket_iam_member" "api_objects" {
  bucket = google_storage_bucket.content.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.api.email}"
}

resource "google_cloud_run_v2_service" "api" {
  # The GA provider (6.50) has no iap_enabled argument, so this one resource
  # uses google-beta. launch_stage is left at its default, since IAP on Cloud
  # Run is GA; if the first plan or apply asks for launch_stage = "BETA", set
  # it here (docs/phases/phase-3.md, "Unverified").
  provider = google-beta

  name     = var.api_service_name
  location = var.region

  iap_enabled = true

  # The run.app URL must be reachable from the phone; IAP, not ingress, is
  # the gate.
  ingress = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.api.email

    # Up to 8 requests at once on one instance: four for the client's
    # prefetch, a feed refresh, an opened article, a feedback event and one
    # spare. One user never needs a second instance.
    max_instance_request_concurrency = var.api_concurrency

    scaling {
      min_instance_count = 0 # scale to zero: nothing is billed while idle
      max_instance_count = var.api_max_instances
    }

    containers {
      image = var.api_image
      args  = ["serve"] # the image's entrypoint is `newsfeed`

      resources {
        limits = {
          cpu    = "1"
          memory = var.api_memory
        }
        # CPU only while a request is being handled, so an idle instance
        # costs nothing before it scales down.
        cpu_idle = true
      }

      dynamic "env" {
        for_each = local.api_env

        content {
          name  = env.key
          value = env.value
        }
      }

      # Not a secret, but from the git-ignored tfvars, so the repo holds no
      # real address. Sensitive, so the plan shows only this value as hidden.
      env {
        name  = "NEWSFEED_OWNER_EMAIL"
        value = var.owner_email
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      client,
      client_version,
    ]
  }

  depends_on = [google_project_service.this]
}

# With IAP on, Cloud Run checks that IAP's service agent may invoke the
# service before passing it a request IAP has allowed. This is the service's
# only invoker grant: no user, no allUsers, no allAuthenticatedUsers. Users
# get through only with roles/iap.httpsResourceAccessor, which is checked by
# IAP, not here.
resource "google_cloud_run_v2_service_iam_member" "iap_invoker" {
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.iap_service_agent}"
}
