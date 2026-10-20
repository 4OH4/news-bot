#!/usr/bin/env bash
# One-off bootstrap for the newsfeed GCP project (ADR-0004, decision 1).
#
# Creates only what Terraform needs before it can run:
#   1. the project, linked to a billing account;
#   2. the APIs Terraform needs to manage everything else (Cloud Storage,
#      Service Usage, Cloud Resource Manager);
#   3. the Terraform state bucket, with uniform access, public access
#      prevention and object versioning.
#
# Each step checks whether its target already exists, so re-running it is
# harmless, but it is not part of later changes: those go through Terraform.
# Only the human runs this, with their own gcloud login.
#
# Usage:
#   infra/bootstrap.sh <project-id> <billing-account-id> <region> [state-bucket]
# The state bucket defaults to <project-id>-tfstate.

set -euo pipefail

if [[ $# -lt 3 || $# -gt 4 ]]; then
  echo "usage: $0 <project-id> <billing-account-id> <region> [state-bucket]" >&2
  exit 2
fi

PROJECT_ID="$1"
BILLING_ACCOUNT="$2"
REGION="$3"
STATE_BUCKET="${4:-${PROJECT_ID}-tfstate}"

log() { printf '[bootstrap] %s\n' "$*"; }

# 1. Project and billing link.
if gcloud projects describe "$PROJECT_ID" --format='value(projectId)' >/dev/null 2>&1; then
  log "project $PROJECT_ID exists"
else
  log "creating project $PROJECT_ID"
  gcloud projects create "$PROJECT_ID" --name="newsfeed"
fi

linked="$(gcloud billing projects describe "$PROJECT_ID" --format='value(billingAccountName)' 2>/dev/null || true)"
if [[ "$linked" == "billingAccounts/$BILLING_ACCOUNT" ]]; then
  log "billing account already linked"
else
  log "linking billing account to $PROJECT_ID"
  gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"
fi

# 2. APIs Terraform needs first; it enables the rest itself (apis.tf).
#    `services enable` is a no-op for APIs that are already on.
log "enabling storage, serviceusage and cloudresourcemanager APIs"
gcloud services enable \
  storage.googleapis.com \
  serviceusage.googleapis.com \
  cloudresourcemanager.googleapis.com \
  --project="$PROJECT_ID"

# 3. Terraform state bucket. Not managed by Terraform, so `terraform destroy`
#    can't delete it; versioning lets a damaged state be rolled back.
if gcloud storage buckets describe "gs://$STATE_BUCKET" --project="$PROJECT_ID" >/dev/null 2>&1; then
  log "state bucket gs://$STATE_BUCKET exists"
else
  log "creating state bucket gs://$STATE_BUCKET in $REGION"
  gcloud storage buckets create "gs://$STATE_BUCKET" \
    --project="$PROJECT_ID" \
    --location="$REGION" \
    --uniform-bucket-level-access \
    --public-access-prevention
fi
log "enabling versioning on gs://$STATE_BUCKET"
gcloud storage buckets update "gs://$STATE_BUCKET" --versioning

log "done. Next: terraform init -backend-config=\"bucket=$STATE_BUCKET\" (see infra/README.md)"
