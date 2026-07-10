FROM python:3.13-slim

WORKDIR /app

# Build toolchain for any deps that compile from source.
# (Originally added for chromadb, since removed — kept until a docker build
# verifies no remaining dep needs it; see docs/deployment.md.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /uvx /bin/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .

# Railway injects $PORT; default to 8000 for local docker run
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uv run uvicorn api.main:app --host 0.0.0.0 --port ${PORT}"]
