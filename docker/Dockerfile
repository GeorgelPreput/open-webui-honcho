# Honcho OpenAPI tool server image (multi-arch: linux/amd64 + linux/arm64; pure-Python).
FROM python:3.12-slim

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install the package (hatchling builds from src/). Copy metadata first for layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system --no-cache .

# Run as an unprivileged user.
RUN useradd -r -u 10001 appuser
USER appuser

EXPOSE 8000

# Health check via stdlib (no curl in slim). Assumes the default TOOL_SERVER_PORT=8000.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"

# Honcho connection + identity defaults come from the environment (1Password-injected at deploy).
CMD ["owui-honcho-tools"]
