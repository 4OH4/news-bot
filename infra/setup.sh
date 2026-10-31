#!/usr/bin/env bash
# First-time cloud setup, as a script (infra/README.md, "First-time setup").
#
# Reads the project, billing account and region from
# infra/terraform/terraform.tfvars, so that file is the only place to edit.
# Asks for confirmation before applying a Terraform plan. Safe to re-run.
# Only the human runs this, with their own gcloud login.
#
# Usage, from anywhere in the repo:
#   infra/setup.sh            everything below, in order
#   infra/setup.sh infra      bootstrap and Terraform only
#   infra/setup.sh deploy     build, push and run the image at HEAD

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$REPO_ROOT/infra/terraform"
TFVARS="$TF_DIR/terraform.tfvars"

log() { printf '\n[setup] %s\n' "$*"; }
die() {
  printf '[setup] error: %s\n' "$*" >&2
  exit 1
}

# Reads a quoted string from terraform.tfvars.
tfvar() {
  sed -n -E "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\"([^\"]*)\".*/\1/p" "$TFVARS" | sed -n 1p
}

load_values() {
  [[ -f "$TFVARS" ]] || die "copy $TFVARS.example to terraform.tfvars and fill it in first"
  # The billing account must never reach this public repo.
  git -C "$REPO_ROOT" check-ignore -q "$TFVARS" ||
    die "git doesn't ignore terraform.tfvars: update your checkout with 'git pull' first"

  PROJECT_ID="$(tfvar project_id)"
  BILLING_ACCOUNT="$(tfvar billing_account)"
  REGION="$(tfvar region)"
  [[ -n "$PROJECT_ID" && -n "$BILLING_ACCOUNT" && -n "$REGION" ]] ||
    die "terraform.tfvars needs project_id, billing_account and region"
  [[ "$PROJECT_ID" != "my-newsfeed-project" && "$BILLING_ACCOUNT" != "000000-000000-000000" ]] ||
    die "terraform.tfvars still has the example values"
  if grep -Eq '^[[:space:]]*alert_email[[:space:]]*=[[:space:]]*"alerts@example\.com"' "$TFVARS"; then
    die "terraform.tfvars still has the example alert_email"
  fi
}

require_tools() {
  local tool
  for tool in "$@"; do
    command -v "$tool" >/dev/null || die "$tool isn't installed"
  done
}

ensure_logins() {
  if [[ -z "$(gcloud auth list --filter=status:ACTIVE --format='value(account)')" ]]; then
    gcloud auth login
  fi
  if ! gcloud auth application-default print-access-token >/dev/null 2>&1; then
    gcloud auth application-default login
  fi
}

# The image tag names a commit, so that commit must be what gets built.
# terraform init writes the lock file, which an agent commits afterwards.
require_clean_tree() {
  [[ -z "$(git -C "$REPO_ROOT" status --porcelain -- . ':!infra/terraform/.terraform.lock.hcl')" ]] ||
    die "commit or stash your changes first, so the image matches its tag"
}

run_infra() {
  log "Bootstrapping $PROJECT_ID"
  "$REPO_ROOT/infra/bootstrap.sh" "$PROJECT_ID" "$BILLING_ACCOUNT" "$REGION"
  gcloud auth application-default set-quota-project "$PROJECT_ID"
  gcloud config set project "$PROJECT_ID"

  log "Planning Terraform changes"
  terraform -chdir="$TF_DIR" init -backend-config="bucket=${PROJECT_ID}-tfstate"
  local status=0
  terraform -chdir="$TF_DIR" plan -detailed-exitcode -out=tfplan || status=$?
  case "$status" in
    0)
      log "No infrastructure changes"
      return
      ;;
    2) ;;
    *) die "terraform plan failed" ;;
  esac

  local answer
  read -r -p "[setup] Apply this plan? Type yes to continue: " answer
  [[ "$answer" == "yes" ]] || die "plan not applied"
  terraform -chdir="$TF_DIR" apply tfplan
}

run_deploy() {
  local repository job schedule bucket tag
  repository="$(terraform -chdir="$TF_DIR" output -raw image_repository)"
  job="$(terraform -chdir="$TF_DIR" output -raw job_name)"
  schedule="$(terraform -chdir="$TF_DIR" output -raw scheduler_job_name)"
  bucket="$(terraform -chdir="$TF_DIR" output -raw content_bucket)"
  tag="$repository/newsfeed:$(git -C "$REPO_ROOT" rev-parse HEAD)"

  # A scheduled run could otherwise overlap the run below, and both would
  # fetch the same sites and write to Firestore.
  # It's already paused if an earlier run failed, and pausing it again may fail.
  log "Pausing the schedule"
  local state
  state="$(gcloud scheduler jobs describe "$schedule" --location="$REGION" --format='value(state)')"
  [[ "$state" != "ENABLED" ]] || gcloud scheduler jobs pause "$schedule" --location="$REGION"
  PAUSED=yes

  log "Building and pushing $tag"
  gcloud auth configure-docker "$REGION-docker.pkg.dev" --quiet
  docker build -f "$REPO_ROOT/backend/Dockerfile" -t "$tag" "$REPO_ROOT"
  docker push "$tag"
  gcloud run jobs update "$job" --region="$REGION" --image="$tag"

  log "Running the job once. The schedule stays paused if this fails"
  gcloud run jobs execute "$job" --region="$REGION" --wait
  gcloud storage ls -l "gs://$bucket/feed.json"

  log "Resuming the schedule"
  gcloud scheduler jobs resume "$schedule" --location="$REGION"
  PAUSED=no

  log "Budgets on the billing account"
  gcloud billing budgets list --billing-account="$BILLING_ACCOUNT"
}

on_exit() {
  local status=$?
  if [[ "$status" -ne 0 && "${PAUSED:-}" == "yes" ]]; then
    printf '[setup] The schedule is still paused. After fixing this, re-run: infra/setup.sh deploy\n' >&2
  fi
}
trap on_exit EXIT

command="${1:-all}"
case "$command" in
  all | infra | deploy) ;;
  *)
    echo "usage: $0 [all|infra|deploy]" >&2
    exit 2
    ;;
esac

require_tools git gcloud terraform
[[ "$command" == "infra" ]] || require_tools docker
load_values
# Makes every gcloud call use the project in terraform.tfvars.
export CLOUDSDK_CORE_PROJECT="$PROJECT_ID"
[[ "$command" == "infra" ]] || require_clean_tree
ensure_logins

[[ "$command" == "deploy" ]] || run_infra
[[ "$command" == "infra" ]] || run_deploy

log "Done"
