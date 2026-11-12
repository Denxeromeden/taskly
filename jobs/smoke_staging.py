"""Validate the staging serving API and the MLflow @staging alias in-cluster.

This runs inside the existing ml-jobs image as a short-lived Job. It replaces
`kubectl exec`/`port-forward`-based checks, which the GitHub deployer role is
deliberately not allowed to use. It prints only status and version information;
the synthetic request body is never logged.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


ALLOWED_PRIORITIES = frozenset({"low", "medium", "high"})
DEFAULT_SERVING_BASE_URL = "http://serving-api.staging.svc.cluster.local:8000"
DEFAULT_MODEL_NAME = "task-priority-classifier"
STAGING_ALIAS = "staging"
SMOKE_TITLE = "Synthetic staging smoke test"
SMOKE_DESCRIPTION = "In-cluster readiness probe; this text is never logged."


class SmokeTestError(RuntimeError):
    """Raised when the staging API does not satisfy the smoke-test contract."""


def _safe_detail(payload: bytes) -> str:
    """Keep server error text short and free of echoed request bodies."""
    try:
        text = payload.decode("utf-8", errors="replace").strip()
    except Exception:
        return "unreadable response body"
    return text[:200] if text else "empty response body"


def request_json(
    url: str,
    method: str = "GET",
    body: Any = None,
    timeout_seconds: float = 10.0,
    include_response_detail: bool = True,
) -> tuple[int, Any]:
    """Perform one JSON request and return the status code and decoded body."""
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        detail = _safe_detail(exc.read()) if include_response_detail else "response body withheld"
        raise SmokeTestError(
            f"{method} {url} returned HTTP {exc.code}: {detail}"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SmokeTestError(f"{method} {url} could not be reached: {type(exc).__name__}") from None

    try:
        decoded = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SmokeTestError(f"{method} {url} returned a non-JSON response") from None
    return status, decoded


def wait_for_ready(
    base_url: str,
    timeout_seconds: float,
    sleep: Any = time.sleep,
) -> None:
    """Poll /ready until it reports ready or the deadline expires."""
    deadline = time.monotonic() + timeout_seconds
    last_error = "no attempt was made"

    while True:
        try:
            status, payload = request_json(f"{base_url}/ready")
            if status == 200 and isinstance(payload, dict) and payload.get("status") == "ready":
                print("ready=ok")
                return
            last_error = f"HTTP {status} with an unexpected body"
        except SmokeTestError as exc:
            last_error = str(exc)

        if time.monotonic() >= deadline:
            raise SmokeTestError(f"/ready did not become ready: {last_error}")
        sleep(2)


def submit_prediction(base_url: str) -> tuple[str, str]:
    """Submit one synthetic prediction and validate its response schema."""
    status, payload = request_json(
        f"{base_url}/predict",
        method="POST",
        body={"title": SMOKE_TITLE, "description": SMOKE_DESCRIPTION},
        include_response_detail=False,
    )
    if status != 200:
        raise SmokeTestError(f"POST /predict returned HTTP {status}")
    if not isinstance(payload, dict):
        raise SmokeTestError("POST /predict did not return a JSON object")

    priority = payload.get("priority")
    model_version = payload.get("model_version")
    if not isinstance(priority, str) or priority not in ALLOWED_PRIORITIES:
        raise SmokeTestError("POST /predict returned an unsupported priority class")
    if not isinstance(model_version, str) or not model_version.strip():
        raise SmokeTestError("POST /predict returned no model version")

    print(f"prediction_priority={priority}")
    print(f"response_model_version={model_version}")
    return priority, model_version


def resolve_staging_version(tracking_uri: str, model_name: str) -> str:
    """Return the concrete version currently referenced by the staging alias."""
    from mlflow.exceptions import MlflowException
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    try:
        version = client.get_model_version_by_alias(model_name, STAGING_ALIAS)
    except MlflowException as exc:
        raise SmokeTestError(
            f"could not resolve the {STAGING_ALIAS!r} alias: {type(exc).__name__}"
        ) from None

    concrete = str(getattr(version, "version", "")).strip()
    if not concrete.isdigit():
        raise SmokeTestError(f"the {STAGING_ALIAS!r} alias has no valid concrete version")
    print(f"staging_alias_version={concrete}")
    return concrete


def run() -> None:
    serving_base_url = os.environ.get("SERVING_BASE_URL", DEFAULT_SERVING_BASE_URL).rstrip("/")
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "").strip()
    model_name = os.environ.get("MODEL_NAME", DEFAULT_MODEL_NAME).strip()
    ready_timeout_raw = os.environ.get("READY_TIMEOUT_SECONDS", "120").strip()

    if not tracking_uri:
        raise SmokeTestError("MLFLOW_TRACKING_URI is required")
    if not model_name:
        raise SmokeTestError("MODEL_NAME must not be empty")
    try:
        ready_timeout = float(ready_timeout_raw)
    except ValueError:
        raise SmokeTestError("READY_TIMEOUT_SECONDS must be a number") from None
    if not 0 < ready_timeout <= 300:
        raise SmokeTestError("READY_TIMEOUT_SECONDS must be between 0 and 300")

    wait_for_ready(serving_base_url, ready_timeout)
    _, response_version = submit_prediction(serving_base_url)
    staging_version = resolve_staging_version(tracking_uri, model_name)

    if response_version != staging_version:
        raise SmokeTestError(
            "response model_version does not match the concrete staging alias version "
            f"(response={response_version}, staging={staging_version})"
        )
    print("smoke=passed")


def main() -> int:
    try:
        run()
    except SmokeTestError as exc:
        print(f"staging smoke failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # keep unexpected failures actionable without a traceback dump
        print(f"staging smoke failed: unexpected {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
