FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py database.py models.py cache.py ./

# Run as non-root — pairs with the Kubernetes PodSecurityContext
# (runAsNonRoot, readOnlyRootFilesystem) production deployments are
# expected to enforce at the cluster level.
RUN useradd --system --no-create-home --uid 10001 taskly
USER taskly

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
