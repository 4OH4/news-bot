# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
# Build from the repo root so config/ can be copied in:
#   docker build -f backend/Dockerfile -t newsfeed .

# Both stages must use the same Python image: the venv links to its interpreter.
# python:3.12-slim-bookworm
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

FROM ${PYTHON_IMAGE} AS builder
# ghcr.io/astral-sh/uv:0.13.0
COPY --from=ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /build
# Dependencies first, so source-only changes reuse the cached layer.
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# Same dependencies plus the optional Playwright group (PROJECT_PLAN.md §11).
FROM builder AS playwright-builder
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --group playwright

# The reader client (PWA), built from client/ alone (#178). This stage runs
# third-party build code, so it gets nothing but what it copies: no credential and
# nothing outside client/. --ignore-scripts stops packages running install hooks.
# The image must equal CLIENT_NODE_IMAGE in the Makefile, tag and digest; a test
# checks they match.
FROM node:24.22.2-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9 AS client-builder
WORKDIR /client
COPY client/package.json client/package-lock.json ./
RUN npm ci --ignore-scripts
COPY client/ ./
RUN npm run build

# Shared by both final targets: the unprivileged user, config and environment.
FROM ${PYTHON_IMAGE} AS base
RUN useradd --create-home --uid 10001 newsfeed \
    && mkdir /data && chown newsfeed:newsfeed /data
COPY config /app/config
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    NEWSFEED_CONFIG_DIR=/app/config \
    NEWSFEED_DATA_DIR=/data
WORKDIR /app
ENTRYPOINT ["newsfeed"]

# Opt-in image with Chromium, for domains flagged `render: playwright`:
#   docker build -f backend/Dockerfile --target playwright -t newsfeed:playwright .
FROM base AS playwright
COPY --from=playwright-builder /app/.venv /app/.venv
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
# Installs Chromium and its system libraries as root, then hands over to the user.
RUN playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chmod -R a+rX /ms-playwright
USER newsfeed

# Keep this stage last: it is what `docker build` builds without --target, and
# it must stay slim.
FROM base AS runtime
COPY --from=builder /app/.venv /app/.venv
# The built client, served by `newsfeed serve` (#175).
COPY --from=client-builder /client/dist /app/client
ENV NEWSFEED_CLIENT_DIR=/app/client
USER newsfeed
