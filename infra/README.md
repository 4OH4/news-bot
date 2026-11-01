# Infrastructure

The production pipeline runs on GCP: Cloud Scheduler starts a Cloud Run Job every
hour, the job runs `newsfeed run` with the Firestore and Cloud Storage adapters,
and a budget emails an alert as spending grows. ADR-0004
([`docs/adr/0004-infrastructure-as-code.md`](../docs/adr/0004-infrastructure-as-code.md))
records the approach: a one-off `gcloud` bootstrap, then everything else in
Terraform. ADR-0007
([`docs/adr/0007-agent-infrastructure-scope.md`](../docs/adr/0007-agent-infrastructure-scope.md))
sets who applies it.

The human does the first-time setup below, including the first `terraform apply`,
and adds secret values. After that, agents apply merged changes with a limited
service account inside the development scope. That account can't create or change
IAM, budget or secret resources, so the human applies those changes too. Its
read-only roles let it refresh them, but never read secret values.

## What is here

| File | What it manages |
|------|-----------------|
| `setup.sh` | Runs the first-time setup below from `terraform.tfvars`. Re-runnable. |
| `bootstrap.sh` | The project, its billing link, the first three APIs and the Terraform state bucket. Run once. |
| `terraform/versions.tf`, `providers.tf` | Terraform and Google provider versions, the `gcs` state backend, and the quota project for user credentials. |
| `terraform/variables.tf` | The values you choose. Project, billing account, region, budget and alert email have no defaults. |
| `terraform/apis.tf` | The remaining APIs. |
| `terraform/budget.tf` | A monthly budget on the project, emailing at 50%, 90% and 100% of actual spend. |
| `terraform/registry.tf` | The Artifact Registry Docker repository, with a cleanup policy (keep the 10 newest images; delete others after 30 days). |
| `terraform/run_job.tf` | The Cloud Run Job (1 vCPU, 1 GiB, 50-minute timeout, no retries) and its runtime service account, which may use Firestore. |
| `terraform/scheduler.tf` | The hourly Cloud Scheduler job and its service account, which may only start the job (`roles/run.invoker`). |
| `terraform/firestore.tf` | The `(default)` Firestore database (Native mode, delete-protected) and the four composite indexes the repository adapter needs. |
| `terraform/storage.tf` | The private content bucket: `articles/` expires after 30 days and `feeds/` after 7 (ADR-0005); the runtime account may read and write its objects. |
| `terraform/secrets.tf` | Secret Manager secrets, none yet. Terraform never holds secret values. |
| `terraform/deploy.tf` | The deploy service account for GitHub Actions (#75): push to the registry, update the job, act as the runtime account. |
| `terraform/wif.tf` | Workload Identity Federation: GCP accepts GitHub's short-lived tokens only from `deploy.yml` on `main`, so no key exists. |
| `terraform/outputs.tf` | Names the deploy workflow and the steps below need. |
| `terraform/.terraform.lock.hcl` | The provider versions, so CI and you use the same ones. Update it with `terraform init -upgrade`. |

The `Infra` workflow (`.github/workflows/infra.yml`) runs `terraform fmt -check`, `init -backend=false` and `validate` on every PR that touches `infra/`. It needs no credentials or secrets (it only downloads the provider) and never plans or applies.

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

### The quick way

`infra/setup.sh` runs every step below, reading the project, billing account and
region from `terraform.tfvars`, so that is the only file to edit. Update your
checkout first, because `.gitignore` must already cover that file:

```bash
git switch main && git pull
cp infra/terraform/terraform.tfvars.example infra/terraform/terraform.tfvars
# edit terraform.tfvars: project_id, billing_account, region, budget_amount, alert_email
infra/setup.sh
```

It stops for you to read the Terraform plan and type `yes`, and it's safe to
re-run. `infra/setup.sh infra` covers the bootstrap and Terraform, and
`infra/setup.sh deploy` builds, pushes and runs the image at `HEAD`. If the run
fails, the schedule stays paused until a later `deploy` succeeds.

### Step by step

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
   in place; it is committed already, and `terraform init` only changes it if the provider version changes.

6. Pause the scheduler first. It has been live since step 5, and Cloud Run
   lets two runs of the job overlap: once the real image is in place, a
   scheduled run could start alongside your manual run in step 7, fetching
   the same sites twice and both writing to Firestore. You resume it at the
   end of step 7.

   ```bash
   gcloud scheduler jobs pause newsfeed-schedule --location=<region>
   ```

   Then build and push the first real image, and point the job at it. Until
   you do, the job runs Google's public sample image, which writes nothing.
   From the repo root:

   ```bash
   REPO="$(terraform -chdir=infra/terraform output -raw image_repository)"
   TAG="$REPO/newsfeed:$(git rev-parse HEAD)"
   gcloud auth configure-docker "<region>-docker.pkg.dev"
   docker build -f backend/Dockerfile -t "$TAG" .
   docker push "$TAG"
   gcloud run jobs update newsfeed --region=<region> --image="$TAG"
   ```

   Once the deploy workflow is set up (see [Deploying on merge](#deploying-on-merge)),
   it does this on every merge to `main`.

7. Run the job once and wait for it:

   ```bash
   gcloud run jobs execute newsfeed --region=<region> --wait
   ```

   It prints the execution id (`newsfeed-xxxxx`); record it on issue #74. Logs
   are in Cloud Logging under the job; `gcloud storage cat
   gs://<project-id>-content/feed.json | head` shows the feed it wrote.

   Once this run has finished, resume the scheduler:

   ```bash
   gcloud scheduler jobs resume newsfeed-schedule --location=<region>
   ```

8. Check the budget exists:

   ```bash
   gcloud billing budgets list --billing-account=<billing-account-id>
   ```

## Deploying on merge

`.github/workflows/deploy.yml` runs when CI passes on a push to `main`. It builds
the image, tags it with the commit SHA, pushes it and updates the job's image, and
the next scheduled run uses it. It skips itself until `GCP_WIF_PROVIDER` is set, so
merges before the setup below stay green. Re-running CI for an older commit on
`main` deploys that commit, so it also works as a rollback. If a deploy fails, the
workflow opens an issue naming the commit and the run (or comments on the open one),
because the old image keeps running unnoticed otherwise.

To set it up, after `wif.tf` is merged and with the repository's admin rights in
`gh`, apply as in [Later changes](#later-changes), then from the repo root:

```bash
tf() { terraform -chdir=infra/terraform output -raw "$1"; }
gh variable set GCP_PROJECT_ID --repo 4OH4/news-bot --body "$(tf project_id)"
gh variable set GCP_REGION --repo 4OH4/news-bot --body "$(tf region)"
gh variable set GCP_IMAGE_REPOSITORY --repo 4OH4/news-bot --body "$(tf image_repository)"
gh variable set GCP_JOB_NAME --repo 4OH4/news-bot --body "$(tf job_name)"
gh variable set GCP_DEPLOY_SERVICE_ACCOUNT --repo 4OH4/news-bot --body "$(tf deploy_service_account)"
gh variable set GCP_WIF_PROVIDER --repo 4OH4/news-bot --body "$(tf workload_identity_provider)"
```

These are identifiers, not secrets. The apply creates IAM, so the human runs it
(ADR-0007). The next merge to `main` then deploys.

If the agents' development service account exists, also give it read access to
the new resources, or its `terraform apply` can't refresh them:

```bash
gcloud projects add-iam-policy-binding <project-id> --member="serviceAccount:<dev-account-email>" --role=roles/iam.workloadIdentityPoolViewer
```

## The development identity

Agents reach the project through a limited service account, not the owner's
login (ADR-0007). The owner sets it up once, with the commands below. They
change IAM, so only the owner runs them. Replace the placeholders:
`<project-id>`, `<billing-account-id>` and `<owner-email>`.

### The account and its roles

```bash
gcloud iam service-accounts create newsfeed-dev --project=<project-id> --display-name="Newsfeed development (agents)"

for role in viewer run.developer cloudscheduler.admin artifactregistry.repoAdmin \
  datastore.indexAdmin secretmanager.viewer iam.workloadIdentityPoolViewer \
  storage.bucketViewer storage.objectViewer; do
  gcloud projects add-iam-policy-binding <project-id> \
    --member="serviceAccount:newsfeed-dev@<project-id>.iam.gserviceaccount.com" \
    --role="roles/$role"
done
```

The state bucket, which Terraform writes to, is the one place the account can
write objects:

```bash
gcloud storage buckets add-iam-policy-binding gs://<project-id>-tfstate \
  --member="serviceAccount:newsfeed-dev@<project-id>.iam.gserviceaccount.com" \
  --role=roles/storage.objectAdmin
```

Billing is read-only, on the billing account:

```bash
gcloud billing accounts add-iam-policy-binding <billing-account-id> \
  --member="serviceAccount:newsfeed-dev@<project-id>.iam.gserviceaccount.com" \
  --role=roles/billing.viewer
```

To deploy, Terraform and the jobs must act as the two runtime accounts, so the
account may use those two and no others:

```bash
for sa in newsfeed-runtime newsfeed-scheduler; do
  gcloud iam service-accounts add-iam-policy-binding \
    "$sa@<project-id>.iam.gserviceaccount.com" --project=<project-id> \
    --member="serviceAccount:newsfeed-dev@<project-id>.iam.gserviceaccount.com" \
    --role=roles/iam.serviceAccountUser
done
```

Finally, the owner may impersonate the account (this is how agents get
short-lived tokens):

```bash
gcloud iam service-accounts add-iam-policy-binding \
  newsfeed-dev@<project-id>.iam.gserviceaccount.com --project=<project-id> \
  --member="user:<owner-email>" --role=roles/iam.serviceAccountTokenCreator
```

### How agents use it

No key file is created. Agents use a separate gcloud configuration, so the
owner's everyday login is not the one they use:

```bash
export CLOUDSDK_CONFIG=~/.config/gcloud-agents
gcloud auth login
gcloud auth application-default login
gcloud config set auth/impersonate_service_account newsfeed-dev@<project-id>.iam.gserviceaccount.com
gcloud config set project <project-id>
```

The login in that directory is the owner's, but every call is made as the
service account through impersonation. The git-ignored
`.claude/settings.local.json` then sets three environment variables for agents:

```json
{
  "env": {
    "CLOUDSDK_CONFIG": "/home/<user>/.config/gcloud-agents",
    "GOOGLE_APPLICATION_CREDENTIALS": "/home/<user>/.config/gcloud-agents/application_default_credentials.json",
    "GOOGLE_IMPERSONATE_SERVICE_ACCOUNT": "newsfeed-dev@<project-id>.iam.gserviceaccount.com"
  }
}
```

`GOOGLE_APPLICATION_CREDENTIALS` is the Application Default Credentials file in
that configuration. If these variables aren't set, an agent doesn't use
`gcloud`, because it would run as the owner's own login.

### The risk

The owner's own gcloud login stays on the machine, so the limit depends on
agents using this configuration as well as on the roles. The roles do limit
what the account can do: it has no Firestore or Cloud Storage write access
outside the state bucket, so it can't delete the feed's data, and it can't
change IAM, billing, budgets or secrets.

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
