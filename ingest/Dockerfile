# syntax=docker/dockerfile:1
#
# Day 5: one small image for both ingest jobs.
#   default command   python -m ingest.sensor_consumer   (Kubernetes Deployment, Day 7)
#   override          python -m ingest.weather --yesterday (Kubernetes CronJob, Day 7)
#
# Build from the repo root (the context needs pyproject.toml, uv.lock, ingest/ and seeds/):
#   docker build -f ingest/Dockerfile -t agri/sensor-consumer:0.1.0 .

# ---------------------------------------------------------------- stage 1: dependencies
# This stage changes only when uv.lock changes, so Docker reuses its cached layer on
# every code-only rebuild. Order layers from least to most frequently changed.
FROM python:3.12-slim AS build
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Behind a TLS-inspecting proxy (common on corporate networks), pass its CA as a
# BuildKit secret:  docker build --secret id=ca,src=/path/to/ca.pem ...
# A secret mount is never written into an image layer. Without it, nothing changes.
RUN --mount=type=secret,id=ca \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca SSL_CERT_FILE=/run/secrets/ca; fi \
 && pip install --no-cache-dir uv \
 # Exact versions from the lock file; runtime dependencies only (no dev, dbt or AI groups).
 && uv export --frozen --no-default-groups --no-emit-project --no-hashes -o requirements.txt \
 # A venv without pip (13 MB saved): uv installs into it from outside.
 && python -m venv --without-pip /opt/venv \
 && uv pip install --no-cache --python /opt/venv/bin/python -r requirements.txt

# ---------------------------------------------------------------- stage 2: runtime
# Starts again from a clean base and copies only the installed packages: no pip
# cache, no uv, no build tools in the final image.
FROM python:3.12-slim
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin agri
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGRI_DATA_DIR=/tmp/agri-data
WORKDIR /app
COPY seeds/ seeds/
COPY transform/seeds/sensors.csv transform/seeds/sensors.csv
COPY ingest/ ingest/
# Never run as root: if the process is compromised, it cannot modify the image.
USER 10001
EXPOSE 8000
CMD ["python", "-m", "ingest.sensor_consumer"]
