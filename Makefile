# Developer entry points. Run from the repo root: `make <target>`.
# The uv project lives in backend/, so Python targets run there.

.DEFAULT_GOAL := help
SHELL := /bin/bash

BACKEND := backend
UV := uv --directory $(BACKEND)

# Absolute paths so the CLI finds config/ and data/ wherever it runs from.
export NEWSFEED_CONFIG_DIR := $(CURDIR)/config
export NEWSFEED_DATA_DIR := $(CURDIR)/data

.PHONY: help setup lint fmt test eval run docker

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

docker: ## Build the backend Docker image (context is the repo root)
	docker build -f $(BACKEND)/Dockerfile -t newsfeed .
