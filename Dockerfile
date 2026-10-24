# Stage 1: build the single page app, with types generated from docs/schemas.
FROM node:20-bookworm-slim AS ui

WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
# gen-types resolves ../../docs/schemas from frontend/scripts.
COPY docs/schemas /docs/schemas
COPY frontend/ ./
RUN npm run types && npm run build

# Stage 2: the python runtime, with the built UI inside it.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    REDRUN_ROOT=/app

WORKDIR /app

COPY pyproject.toml ./
COPY redrun ./redrun
RUN pip install .

COPY samples ./samples
COPY docs ./docs
COPY --from=ui /ui/dist ./frontend/dist
RUN mkdir -p /app/logs

EXPOSE 8000

CMD ["uvicorn", "redrun.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
