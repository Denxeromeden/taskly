# syntax=docker/dockerfile:1

# Build from the repository root:
#   docker build -f images/serving/Dockerfile -t mlflowops/serving:<git-sha> .

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    TMPDIR=/tmp \
    XDG_CACHE_HOME=/tmp/.cache

RUN groupadd --gid 10001 serving \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin serving

WORKDIR /app

# The shared constraints keep model deserialization compatible with training.
COPY constraints/ml-runtime.txt /tmp/ml-runtime.txt
COPY serving/requirements.txt /tmp/serving-requirements.txt
RUN pip install --no-cache-dir --no-compile \
      -r /tmp/serving-requirements.txt \
      -c /tmp/ml-runtime.txt \
 && pip check \
 && rm -f /tmp/serving-requirements.txt /tmp/ml-runtime.txt

# Only application code is copied; model artifacts are loaded from MLflow at startup.
COPY --chown=10001:10001 serving/app /app/serving/app

USER 10001:10001

EXPOSE 8000

CMD ["uvicorn", "serving.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
