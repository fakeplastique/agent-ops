# Build context is the repo root: the image also carries scripts/migrate.sh (Railway pre-deploy).
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
WORKDIR /srv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY scripts/migrate.sh scripts/db_roles.sql ./scripts/
# Fixed non-root uid so the host never needs to know the user name.
RUN useradd --uid 10001 --no-create-home app
USER 10001
ENV PATH="/srv/.venv/bin:$PATH" MIGRATE_PYTHON=python BACKEND_DIR=/srv
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips=*"]
