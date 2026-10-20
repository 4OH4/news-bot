# Values the human chooses. Anything personal or account-specific (project id,
# billing account, region, budget, alert email) has no default; set it in the
# git-ignored terraform.tfvars (see terraform.tfvars.example).

# --- Required: no defaults ---------------------------------------------------

variable "project_id" {
  description = "GCP project id, as created by bootstrap.sh."
  type        = string
}

variable "billing_account" {
  description = "Billing account id (XXXXXX-XXXXXX-XXXXXX) that the budget watches."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Scheduler, Artifact Registry and the content bucket, e.g. europe-west2."
  type        = string
}

variable "budget_amount" {
  description = "Monthly budget, in whole units of the billing account's currency."
  type        = number

  validation {
    condition     = var.budget_amount > 0 && floor(var.budget_amount) == var.budget_amount
    error_message = "budget_amount must be a positive whole number."
  }
}

variable "alert_email" {
  description = "Email address that receives the budget alerts."
  type        = string
  sensitive   = true
}

# --- Optional: safe defaults -------------------------------------------------

variable "budget_thresholds" {
  description = "Fractions of the budget (actual spend) that trigger an alert."
  type        = list(number)
  default     = [0.5, 0.9, 1.0]
}

variable "firestore_location" {
  description = "Firestore location id. Defaults to var.region; a multi-region such as eur3 or nam5 also works."
  type        = string
  default     = null
}

variable "content_retention_days" {
  description = "Days before article content under articles/ is deleted (ADR-0005). Also passed to the job."
  type        = number
  default     = 30
}

variable "feed_copy_retention_days" {
  description = "Days the timestamped feed copies under feeds/ are kept (ADR-0005 decision 5, ADR-0006)."
  type        = number
  default     = 7
}

variable "image" {
  description = <<-EOT
    Container image for the first apply only. Terraform ignores later changes to
    the job's image, which the deploy workflow (#75) or `gcloud run jobs update`
    sets. The default is Google's public sample job image, so the first apply
    works before any image has been pushed.
  EOT
  type    = string
  default = "us-docker.pkg.dev/cloudrun/container/job:latest"
}

variable "job_name" {
  description = "Name of the Cloud Run Job."
  type        = string
  default     = "newsfeed"
}

variable "job_cpu" {
  description = "vCPUs for one pipeline run."
  type        = string
  default     = "1"
}

variable "job_memory" {
  description = "Memory for one pipeline run."
  type        = string
  default     = "1Gi"
}

variable "job_timeout_seconds" {
  description = "Maximum length of one run. Kept under the hourly interval so runs don't overlap."
  type        = number
  default     = 1800
}

variable "schedule" {
  description = "Cron schedule for the pipeline (Cloud Scheduler syntax)."
  type        = string
  default     = "0 * * * *"
}

variable "schedule_time_zone" {
  description = "Time zone the schedule is read in."
  type        = string
  default     = "Etc/UTC"
}

variable "secret_ids" {
  description = "Secret Manager secrets to create (containers only; values are added with gcloud). Empty for now."
  type        = set(string)
  default     = []
}
