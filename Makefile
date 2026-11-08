# Developer entry points. Run from the repo root: `make <target>`.
# The uv project lives in backend/, so Python targets run there.

.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND := backend
UV := uv --directory $(BACKEND)

# Absolute paths so the CLI finds config/ and data/ wherever it runs from.
export NEWSFEED_CONFIG_DIR := $(CURDIR)/config
export NEWSFEED_DATA_DIR := $(CURDIR)/data

# Client tooling runs in pinned images (this machine has no Node). Pin by tag and digest.
# The Node image is read from the Dockerfile's client-builder FROM line, so a Dependabot
# bump of that line moves the image build and these targets together (#256).
CLIENT_NODE_IMAGE := $(shell sed -n 's/^FROM \(node:[^ ]*\) AS client-builder$$/\1/p' $(BACKEND)/Dockerfile)
ifeq ($(CLIENT_NODE_IMAGE),)
$(error CLIENT_NODE_IMAGE is empty: no client-builder FROM line in $(BACKEND)/Dockerfile)
endif
# The tag must equal the @playwright/test version in client/package.json.
CLIENT_PLAYWRIGHT_IMAGE := mcr.microsoft.com/playwright:v1.59.0-noble@sha256:11707c5282fec52f0705aad3a23f46b3409b3de4aed378b3dbe118d8b8c87b67
# Runs as the caller, so node_modules is never root-owned. Only client/ (writable) and
# docs/ (read-only, for tests that read the docs) are mounted.
CLIENT_DOCKER = docker run --rm --user $(shell id -u):$(shell id -g) \
	-e HOME=/tmp -e npm_config_cache=/tmp/.npm -e CI=$(CI) \
	-v $(CURDIR)/client:/work/client -v $(CURDIR)/docs:/work/docs:ro -w /work/client

.PHONY: help setup lint fmt test eval run inspect inspect-gcp docker \
	client-install client-add client-audit client-lint client-test client-build client-e2e

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

# pre-commit runs from the repo root (not backend/) so it picks up the root config.
setup: ## Install dependencies with uv and install the pre-commit hooks
	$(UV) sync
	uv run --project $(BACKEND) pre-commit install

lint: ## ruff check + ruff format --check + mypy (strict)
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy

fmt: ## Auto-format and apply safe lint fixes
	$(UV) run ruff check --fix .
	$(UV) run ruff format .

test: ## Run unit tests (network disabled, live tests excluded)
	$(UV) run pytest --cov=newsfeed --cov-report=term-missing

eval: ## Run the extraction evaluation over saved snapshots (EVAL_ARGS="--domain d")
	$(UV) run newsfeed eval run $(EVAL_ARGS)

run: ## Run the full pipeline locally
	@mkdir -p "$(NEWSFEED_DATA_DIR)"
	$(UV) run newsfeed run

inspect: ## Query stored articles and feedback, read-only, local store (ARGS="articles --since 7d")
	uv run --project $(BACKEND) newsfeed inspect $(ARGS)

# Runs from backend/ so a root .env can't override the live-store settings.
inspect-gcp: ## Same, against the live GCP store (ARGS="articles --source bbc-news")
	@project="$$(gcloud config get-value project 2>/dev/null)"; \
	if [ -z "$$project" ]; then \
		echo "error: gcloud has no project set, so the agent gcloud configuration isn't set up (infra/README.md, 'How agents use it')" >&2; \
		exit 1; \
	fi; \
	NEWSFEED_STORAGE=gcp NEWSFEED_GCP_PROJECT="$$project" NEWSFEED_GCS_BUCKET="$$project-content" \
		$(UV) run newsfeed inspect $(ARGS)

docker: ## Build the backend Docker image (context is the repo root)
	docker build -f $(BACKEND)/Dockerfile -t newsfeed .

client-install: ## Install client dependencies from the lockfile (scripts disabled)
	$(CLIENT_DOCKER) $(CLIENT_NODE_IMAGE) npm ci --ignore-scripts

client-add: ## Add client packages and update the lockfile (make client-add PKG="-D name")
	$(CLIENT_DOCKER) $(CLIENT_NODE_IMAGE) npm install --ignore-scripts $(PKG)

client-audit: ## npm audit on the client (make client-audit AUDIT_ARGS="--omit=dev")
	$(CLIENT_DOCKER) $(CLIENT_NODE_IMAGE) npm audit $(AUDIT_ARGS)

client-lint: ## ESLint and tsc --noEmit on the client
	$(CLIENT_DOCKER) $(CLIENT_NODE_IMAGE) npm run lint

client-test: ## Vitest unit tests for the client
	$(CLIENT_DOCKER) $(CLIENT_NODE_IMAGE) npm test

client-build: ## Production build of the client into client/dist
	$(CLIENT_DOCKER) $(CLIENT_NODE_IMAGE) npm run build

# No -p flag: the preview server is private to the container's network namespace, so
# parallel runs in different worktrees cannot collide on the port.
client-e2e: ## Playwright tests against the production build
	$(CLIENT_DOCKER) --ipc=host $(CLIENT_PLAYWRIGHT_IMAGE) npm run e2e
