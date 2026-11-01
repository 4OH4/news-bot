# Workload Identity Federation (#75): GCP trusts the short-lived tokens GitHub
# issues to the deploy workflow, so no service-account key exists anywhere.
#
# The provider rejects every token except one from deploy.yml on the main
# branch of this repository. Repository and branch alone are not enough: any
# workflow started by an event on the default branch gets a token with
# ref=refs/heads/main, and claude.yml already holds id-token: write on such
# events. So the condition also pins the workflow file and the event that
# starts it. Renaming deploy.yml or changing its trigger needs a change here.
#
# Google keeps a deleted pool for 30 days and won't reuse its id meanwhile.

locals {
  github_repository = "4OH4/news-bot"
  # GitHub's numeric id, which unlike the name can't pass to another repository
  # if this one is renamed or deleted.
  github_repository_id = "1414564113"
  github_deploy_branch = "refs/heads/main"
  # The workflow_ref claim, which names the running workflow file.
  github_deploy_workflow = "${local.github_repository}/.github/workflows/deploy.yml@${local.github_deploy_branch}"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  description               = "Trusts GitHub's tokens for the newsfeed deploy workflow."

  depends_on = [google_project_service.this]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "news-bot"
  display_name                       = "news-bot deploy workflow"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  attribute_condition = join(" && ", [
    "assertion.repository == \"${local.github_repository}\"",
    "assertion.repository_id == \"${local.github_repository_id}\"",
    "assertion.ref == \"${local.github_deploy_branch}\"",
    "assertion.workflow_ref == \"${local.github_deploy_workflow}\"",
    "assertion.event_name == \"workflow_run\"",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# Only identities that passed the provider's condition can match this member.
resource "google_service_account_iam_member" "deploy_via_wif" {
  service_account_id = google_service_account.deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${local.github_repository}"
}
