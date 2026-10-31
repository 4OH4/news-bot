# Monthly budget on this project, with email alerts at each threshold (NFR-2).
#
# A budget only alerts; it never stops spending. The amount is in the billing
# account's own currency (no currency_code is set, so it can't mismatch).
# Creating it needs a billing role on the billing account (Billing Account
# Administrator or Billing Account Costs Manager), not just project Owner.

resource "google_monitoring_notification_channel" "budget_email" {
  display_name = "newsfeed budget alerts"
  type         = "email"

  labels = {
    email_address = var.alert_email
  }

  depends_on = [google_project_service.this]
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "newsfeed monthly budget"

  budget_filter {
    projects        = ["projects/${data.google_project.this.number}"]
    calendar_period = "MONTH"
  }

  amount {
    specified_amount {
      units = tostring(var.budget_amount)
    }
  }

  dynamic "threshold_rules" {
    for_each = var.budget_thresholds

    content {
      threshold_percent = threshold_rules.value
      spend_basis       = "CURRENT_SPEND"
    }
  }

  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.budget_email.id]
    disable_default_iam_recipients   = false
  }

  depends_on = [google_project_service.this]
}
