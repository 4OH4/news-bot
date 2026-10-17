# ADR-0004: Terraform for GCP infrastructure, with a small `gcloud` bootstrap

- **Status:** Accepted
- **Date:** 2026-10-18
- **Related:** #72 (this decision); blocks #74 (GCP setup) and #75 (deploy workflow); ADR-0003 (Phase 1 go); ADR-0005 (#73, content retention, which sets the bucket lifecycle rule). See `PROJECT_PLAN.md` §5.2 (Infra row), §6 Phase 2, §11; NFR-2 (low cost, budget alerts), NFR-6 (secrets in Secret Manager); `CLAUDE.md`, "Things agents must not do".

## Context

Phase 2 runs the pipeline on GCP. It needs a project linked to billing, a budget alert, an Artifact Registry repository for the image, a Cloud Run Job started hourly by Cloud Scheduler, a Firestore database, a Cloud Storage bucket, Secret Manager, a least-privilege runtime service account (#74) and, for the deploy workflow, Workload Identity Federation (WIF) with a deploy service account (#75). The plan prefers Terraform but leaves scripted `gcloud` open (§5.2, §11).

The forces:

- **Small and single-user.** About a dozen resources, one environment (production), one person. Whatever we pick must not cost more to maintain than the infrastructure itself.
- **Only the human applies changes.** Agents write infrastructure code but never apply it, and CI never holds credentials that can change infrastructure (`CLAUDE.md`). So the human must be able to see exactly what a merged change will do before it happens, without reading the code line by line.
- **Low cost (NFR-2).** Any supporting resource, such as state storage, must stay within the free tier or close to it.
- **Secrets (NFR-6).** Secret values must stay out of the public repo, and ideally out of anything the tool stores.
- **Some resources can't be created by the tool that manages them.** Terraform keeps its state in a bucket, and that bucket needs a project, so the project and state bucket come first.

## Decision

**We will define the GCP infrastructure in Terraform, preceded by a one-off `gcloud` bootstrap script.** Only the human runs either, from their own machine with their own credentials.

**1. Bootstrap (`gcloud`, run once).** `infra/bootstrap.sh` does only what has to exist before Terraform can run:

- create the project and link it to the billing account;
- enable the APIs Terraform needs before it can manage anything else (Cloud Storage, Service Usage, Cloud Resource Manager); Terraform enables the rest;
- create the state bucket in the chosen region, with uniform bucket-level access, public access prevention and object versioning (so a damaged state can be rolled back).

The script takes the project id, billing account and region as arguments, checks whether each thing already exists before creating it, and prints what it did. It is not re-run for later changes.

**2. Everything else in Terraform.** One root module in `infra/terraform/`, no child modules and no workspaces, because there is one environment. It covers the remaining APIs, the budget and its alert thresholds, Artifact Registry, the Cloud Run Job, the Scheduler job, Firestore (Native mode), the content bucket and its lifecycle rule (ADR-0005), Secret Manager secrets, service accounts and their IAM bindings, and (from #75) the WIF pool, provider and deploy service account.

**3. State.** State lives in the bootstrap bucket through Terraform's `gcs` backend, which locks state natively with a lock object in the same bucket, so no separate lock table is needed. The bucket name is supplied at `terraform init` time with `-backend-config` (partial backend configuration), so no project-specific value is committed. The state bucket is not managed by Terraform itself, so `terraform destroy` can't delete it.

**4. Versions.** `versions.tf` sets a minimum Terraform version and pins the Google provider to one major version. The provider lock file (`.terraform.lock.hcl`) is committed so every `init` uses the same provider build. OpenTofu reads the same files and may be used instead; the code avoids features unique to either.

**5. Who applies changes.** Only the human, from their machine, using Application Default Credentials (`gcloud auth application-default login`). Every infrastructure PR lists the exact commands, normally:

```bash
cd infra/terraform
terraform init -backend-config="bucket=<state bucket>"
terraform plan -out=tfplan
terraform apply tfplan
```

Agents write and format the code (`terraform fmt`) but never run `init` against the real backend, `plan`, `apply` or `destroy`, and never hold GCP credentials. CI may run `terraform fmt -check` and `terraform validate` (with `init -backend=false`, which needs no credentials); it never runs `plan` or `apply`. The deploy workflow (#75) updates only the Cloud Run Job's image, through WIF, and does not run Terraform.

**6. Directory layout.**

```
infra/
├── README.md                    # what each part is, apply steps, teardown
├── bootstrap.sh                 # one-off gcloud: project, billing link, APIs, state bucket
└── terraform/
    ├── versions.tf              # required Terraform and provider versions, gcs backend block
    ├── providers.tf
    ├── variables.tf             # values the human chooses; no defaults with personal data
    ├── terraform.tfvars.example # placeholder values only; the real terraform.tfvars is git-ignored
    ├── apis.tf
    ├── budget.tf
    ├── registry.tf
    ├── run_job.tf               # Cloud Run Job and its runtime service account
    ├── scheduler.tf
    ├── firestore.tf
    ├── storage.tf               # content bucket and lifecycle rule
    ├── secrets.tf
    ├── wif.tf                   # added by #75
    ├── outputs.tf               # identifiers for the deploy workflow's repository variables
    └── .terraform.lock.hcl
```

Files are split by resource area so #74 and #75 can be reviewed separately. #74 may adjust the file names and split; what this ADR decides is the split between bootstrap and Terraform, and the state, apply and secrets rules.

**7. Secrets and personal values stay out of the repo and out of state.** Terraform creates Secret Manager *secrets* (the named containers and who can read them) but never their values, because anything Terraform sets is stored in plain text in the state file. The human adds values with `gcloud secrets versions add` and the Cloud Run Job reads them at run time. The project id, billing account id, region, budget amount and alert email are Terraform variables with no defaults; the human keeps them in a git-ignored `terraform.tfvars`. `.gitignore` gains `infra/**/.terraform/`, `*.tfstate*`, `*.tfvars` (but not `*.tfvars.example`), `tfplan` and `crash.log` in #74, before any of these files can exist.

### Alternatives considered

- **Scripted `gcloud` only.** Nothing new to install and no state to store. Rejected because there is no plan step: the human would have to read a script to know what running it will change, which defeats "the human applies" as a review step. Scripts also aren't idempotent without hand-written existence checks for every resource, can't show drift (a setting changed in the console), and teardown has to be written separately and kept in step.
- **Terraform for everything, including the project and state bucket.** Possible with local state for a first apply followed by a state migration, but that adds a fragile one-time procedure to save a few lines of shell. The bootstrap is small enough that leaving it outside Terraform is the simpler choice.
- **Terraform with local state only.** No bucket needed, but the state (which records every resource and can hold sensitive attributes) would sit on one laptop with no versioning, and would be lost with it. The state bucket costs a few cents a month at most.
- **Pulumi, or Google Cloud Infrastructure Manager.** Pulumi would mean writing infrastructure in Python with its own state service or backend; Infrastructure Manager runs Terraform inside GCP and needs its own service account with broad rights. Neither is worth an extra tool or service at this size.

## Consequences

**Positive**

- `terraform plan` shows the human exactly what a merged change will create, change or destroy before anything happens, which is what makes "agents write, the human applies" safe.
- Drift is visible: a setting changed in the console shows up in the next plan.
- Teardown is `terraform destroy` followed by deleting the project, rather than a hand-written script.
- Agents can write infrastructure code against well-documented Google provider resources, and reviewers can check it statically.

**Negative**

- One more tool to install (Terraform or OpenTofu), and a state bucket to keep. The bucket is versioned, so state mistakes can be rolled back, but it must not be deleted while the infrastructure exists.
- More files than a single script for about a dozen resources.
- **The Cloud Run Job's image is changed outside Terraform** by the deploy workflow (#75). The job resource must ignore changes to its container image (a `lifecycle { ignore_changes = [...] }` on the image field), or every plan would try to roll the job back to the image it was created with. Its first apply needs an image that already exists: #74 either uses a public placeholder image or has the human push one first, and says which.
- **The budget resource needs a quota project.** With user credentials, the Cloud Billing Budgets API needs `billing_project` and `user_project_override` set on the provider; #74 must set these or the budget fails to apply.
- Firestore and the content bucket hold the data. #74 should give them deletion protection (`deletion_protection` or `prevent_destroy`) so a mistaken plan can't remove them.

**Follow-up work**

- #74 writes the bootstrap script, the Terraform for the core resources, the `.gitignore` entries and `infra/README.md` with the apply and teardown steps.
- #75 adds `wif.tf` and the deploy workflow, and lists the repository variables taken from `outputs.tf`.
- Optional: a CI job running `terraform fmt -check` and `validate` once `infra/terraform/` has code, as its own issue.

**Revisit when**

- a second environment (for example staging) is needed, which would call for modules or workspaces;
- the infrastructure moves off GCP or most of it stops being managed by Terraform;
- someone other than the human needs to apply changes, for example from CI, which would also need a new decision on credentials.
