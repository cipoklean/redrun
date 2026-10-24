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
RUN mkdir -p /app/logs

EXPOSE 8000

CMD ["uvicorn", "redrun.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
