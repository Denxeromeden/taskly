# syntax=docker/dockerfile:1

# Build from the repository root:
#   docker build -f images/ml-jobs/Dockerfile -t mlflowops/ml-jobs:<git-sha> .

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp

# Non-root runtime identity; no writable home directory is created.
RUN groupadd --gid 10001 mljobs \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin mljobs

# MLflow's client shells out to git when it records run metadata; without it
# every run logs a "Failed to import Git" warning.
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt

WORKDIR /app

# Dependencies first so source changes do not invalidate the install layer.
# The constraint file is what keeps the training and serving ML runtime identical.
COPY constraints/ml-runtime.txt /tmp/ml-runtime.txt
COPY model/requirements.txt /tmp/model-requirements.txt
RUN pip install --no-cache-dir --no-compile \
      -r /tmp/model-requirements.txt \
      -c /tmp/ml-runtime.txt \
 && pip check \
 && rm -f /tmp/model-requirements.txt /tmp/ml-runtime.txt

# Training reads these paths relative to /app, so Jobs use the same commands as
# a local checkout: `python model/train.py`, `python model/generate_data.py`.
COPY --chown=10001:10001 model/train.py model/generate_data.py /app/model/

USER 10001:10001
