# syntax=docker/dockerfile:1
# Build from the repo root so config/ can be copied in:
#   docker build -f backend/Dockerfile -t newsfeed .

# Both stages must use the same Python image: the venv links to its interpreter.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /bin/uv
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

FROM ${PYTHON_IMAGE} AS runtime
RUN useradd --create-home --uid 10001 newsfeed \
    && mkdir /data && chown newsfeed:newsfeed /data
COPY --from=builder /app/.venv /app/.venv
COPY config /app/config
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    NEWSFEED_CONFIG_DIR=/app/config \
    NEWSFEED_DATA_DIR=/data
WORKDIR /app
USER newsfeed
ENTRYPOINT ["newsfeed"]
