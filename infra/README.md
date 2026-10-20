# Infrastructure

The production pipeline runs on GCP: Cloud Scheduler starts a Cloud Run Job every
hour, the job runs `newsfeed run` with the Firestore and Cloud Storage adapters,
and a budget emails an alert as spending grows. ADR-0004
([`docs/adr/0004-infrastructure-as-code.md`](../docs/adr/0004-infrastructure-as-code.md))
records the approach: a one-off `gcloud` bootstrap, then everything else in
Terraform, applied only by the human from their own machine.

Agents write and review this code. They never run `terraform init` against the
real backend, `plan`, `apply` or `destroy`, or any `gcloud` command that changes
resources.

## What is here

| File | What it manages |
|------|-----------------|
| `bootstrap.sh` | The project, its billing link, the first three APIs and the Terraform state bucket. Run once. |
| `terraform/versions.tf`, `providers.tf` | Terraform and Google provider versions, the `gcs` state backend, and the quota project for user credentials. |
| `terraform/variables.tf` | The values you choose. Project, billing account, region, budget and alert email have no defaults. |
| `terraform/apis.tf` | The remaining APIs. |
| `terraform/budget.tf` | A monthly budget on the project, emailing at 50%, 90% and 100% of actual spend. |
| `terraform/registry.tf` | The Artifact Registry Docker repository, with a cleanup policy (keep the 10 newest images; delete others after 30 days). |
| `terraform/run_job.tf` | The Cloud Run Job (1 vCPU, 1 GiB, 30-minute timeout, no retries) and its runtime service account, which may use Firestore. |
| `terraform/scheduler.tf` | The hourly Cloud Scheduler job and its service account, which may only start the job (`roles/run.invoker`). |
| `terraform/firestore.tf` | The `(default)` Firestore database (Native mode, delete-protected) and the four composite indexes the repository adapter needs. |
| `terraform/storage.tf` | The private content bucket: `articles/` expires after 30 days and `feeds/` after 7 (ADR-0005); the runtime account may read and write its objects. |
| `terraform/secrets.tf` | Secret Manager secrets, none yet. Terraform never holds secret values. |
| `terraform/deploy.tf` | The deploy service account for GitHub Actions (#75): push to the registry, update the job, act as the runtime account. #75 adds `wif.tf`. |
| `terraform/outputs.tf` | Names the deploy workflow and the steps below need. |

Firestore and the content bucket hold the data, so both have `prevent_destroy`:
a plan that would delete them fails instead. The content bucket is private;
`feed.json` is uploaded with `Cache-Control: no-cache` by the `GcsBlobStore`
adapter, so any later public or proxied access never serves a stale feed.

## First-time setup

You need `gcloud`, Terraform 1.6 or later, and Docker. Your account needs
Owner on the project (the bootstrap makes you Owner of a new project) and,
on the billing account, Billing Account Administrator or Billing Account
Costs Manager to create the budget.

Choose the values first: a globally unique project id, your billing account id
(`gcloud billing accounts list`), a region (for example `europe-west2`), a monthly
budget in your billing currency, and the alert email address.

1. Log in, for gcloud and for Terraform (Application Default Credentials):

   ```bash
   gcloud auth login
   gcloud auth application-default login
   ```

2. Bootstrap the project and the state bucket (from the repo root):

   ```bash
   infra/bootstrap.sh <project-id> <billing-account-id> <region>
   ```

3. Make the new project the quota project for your Application Default
   Credentials, so the Service Usage and Budgets APIs accept your calls:

   ```bash
   gcloud auth application-default set-quota-project <project-id>
   gcloud config set project <project-id>
   ```

4. Fill in the variables. `terraform.tfvars` is git-ignored:

   ```bash
   cd infra/terraform
   cp terraform.tfvars.example terraform.tfvars
   # edit terraform.tfvars: project_id, billing_account, region, budget_amount, alert_email
   ```

5. Initialise, plan, and apply after reading the plan:

   ```bash
   terraform init -backend-config="bucket=<project-id>-tfstate"
   terraform plan -out=tfplan
   terraform apply tfplan
   ```

   The first apply takes a few minutes; the Firestore indexes keep building in
   the background for a few more. `init` writes `.terraform.lock.hcl`: leave it
   in place, and an agent commits it.

6. Build and push the first real image, and point the job at it. Until you do,
   the job runs Google's public sample image, which writes nothing. From the
   repo root:

   ```bash
   REPO="$(terraform -chdir=infra/terraform output -raw image_repository)"
   TAG="$REPO/newsfeed:$(git rev-parse HEAD)"
   gcloud auth configure-docker "<region>-docker.pkg.dev"
   docker build -f backend/Dockerfile -t "$TAG" .
   docker push "$TAG"
   gcloud run jobs update newsfeed --region=<region> --image="$TAG"
   ```

   After #75 is merged, the deploy workflow does this on every merge to `main`.

7. Run the job once and wait for it:

   ```bash
   gcloud run jobs execute newsfeed --region=<region> --wait
   ```

   It prints the execution id (`newsfeed-xxxxx`); record it on issue #74. Logs
   are in Cloud Logging under the job; `gcloud storage cat
   gs://<project-id>-content/feed.json | head` shows the feed it wrote.

8. Check the budget exists:

   ```bash
   gcloud billing budgets list --billing-account=<billing-account-id>
   ```

## Later changes

A PR that changes `infra/terraform/` lists its commands; normally:

```bash
cd infra/terraform
terraform init -backend-config="bucket=<project-id>-tfstate"
terraform plan -out=tfplan
terraform apply tfplan
```

Terraform ignores the job's image, which the deploy workflow sets. Everything
else changed outside Terraform (for example in the console) shows up in the next
plan as drift.

To add a secret: add its id to `secret_ids` in `terraform.tfvars`, apply, then
`gcloud secrets versions add <id> --data-file=-` and reference it from the job
in a PR.

## Teardown

Delete the project. That removes everything, including the state bucket and the
data:

```bash
gcloud projects delete <project-id>
```

`terraform destroy` alone stops at the protected Firestore database and content
bucket, by design (ADR-0004).
