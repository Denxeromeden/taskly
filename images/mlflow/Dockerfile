# syntax=docker/dockerfile:1

# Build from the repository root:
#   docker build -f images/mlflow/Dockerfile -t mlflowops/mlflow:<git-sha> .

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

# Non-root runtime identity; no writable home directory is created.
RUN groupadd --gid 10001 mlflow \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin mlflow

WORKDIR /app

# Dependencies first so entrypoint changes do not invalidate the install layer.
COPY images/mlflow/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --no-compile -r /tmp/requirements.txt \
 && rm -f /tmp/requirements.txt

COPY --chown=10001:10001 images/mlflow/entrypoint.py /app/entrypoint.py

USER 10001:10001

EXPOSE 5000

ENTRYPOINT ["python", "/app/entrypoint.py"]
