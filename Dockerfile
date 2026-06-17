# Avatar Engine — single image, run as API or worker.
FROM python:3.11-slim

LABEL org.opencontainers.image.title="avatar-engine" \
      org.opencontainers.image.description="Temporal for AI agents — a Postgres-native durable execution engine." \
      org.opencontainers.image.source="https://github.com/avatar-runtime/avatar-engine" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY requirements.txt pyproject.toml README.md LICENSE NOTICE ./
RUN pip install --no-cache-dir -r requirements.txt

COPY avatar ./avatar
RUN pip install --no-cache-dir -e .

EXPOSE 8080
# Default to the API; the worker service overrides `command` in compose.
CMD ["avatar", "serve", "--host", "0.0.0.0", "--port", "8080"]
