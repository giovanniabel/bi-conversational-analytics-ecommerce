FROM python:3.11-slim

WORKDIR /app

# curl is needed by the HEALTHCHECK below; python:3.11-slim doesn't ship it
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# uv gives us reproducible, locked installs (uv.lock) and a much faster build
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Install dependencies first (cached layer) before copying the rest of the app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy application
COPY . .
RUN uv sync --frozen --no-dev

# 8501 = Streamlit UI, 8000 = FastAPI/Metabase UI. One image serves both;
# docker-compose picks which by overriding `command`.
EXPOSE 8501 8000

# Streamlit is the default. CMD (not ENTRYPOINT) so `command:` in compose can
# swap in uvicorn for the Metabase web app without an entrypoint override.
# Per-service healthchecks live in docker-compose.yaml, since the right probe
# depends on which command is running.
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
