# --- Stage 1: Build dependencies ---
FROM ghcr.io/astral-sh/uv:python3.11-alpine AS builder

# Set working directory and configure environment variables
WORKDIR /app
ENV UV_PYTHON=python3.11
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PROJECT_ENVIRONMENT="/usr/local"

# 1. Cache and install dependencies first (leverages Docker layer caching)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev


# --- Stage 2: Final minimal production image ---
FROM python:3.11-alpine AS runner

WORKDIR /app

# Copy the globally installed packages from the builder stage
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy your actual application code
COPY ./src /app

# 7. Use a non-root user for security
RUN adduser -D -S -u 8888 motu && chown -R motu /app
USER motu

# Expose ports or define execution entry points
EXPOSE 5000
ENTRYPOINT ["hypercorn", "api:app"]
CMD ["--bind", "0.0.0.0:5000"]
