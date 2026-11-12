"""Verify that the public production prediction reports the expected version.

The protected production workflows run this inside the ml-jobs namespace after
the production Deployment is available. The public Gateway route only exposes
``POST /predict``, so the check resolves the concrete ``@production`` alias from
MLflow, submits one synthetic prediction through the public HTTPS endpoint and
requires the response version to match the approved alias exactly.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any


ALLOWED_PRIORITIES = frozenset({"low", "medium", "high"})
DEFAULT_MODEL_NAME = "task-priority-classifier"
PRODUCTION_ALIAS = "production"
SYNTHETIC_TITLE = "Synthetic production verification"
SYNTHETIC_DESCRIPTION = "In-cluster public-route check; this text is never logged."


class VerificationError(RuntimeError):
    """Raised when the public production check does not satisfy the contract."""


def _tls_context() -> ssl.SSLContext:
    verify_raw = os.environ.get("TLS_VERIFY", "true").strip().lower()
    if verify_raw in {"1", "true", "yes"}:
        return ssl.create_default_context()
    if verify_raw in {"0", "false", "no"}:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    raise VerificationError("TLS_VERIFY must be true or false")


def request_json(
    url: str,
    method: str = "GET",
    body: Any = None,
    timeout_seconds: float = 10.0,
) -> tuple[int, Any]:
    """Perform one JSON request without logging the request or response body."""
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    context = _tls_context() if url.startswith("https://") else None
    try:
        if context is None:
            response_context = urllib.request.urlopen(request, timeout=timeout_seconds)
        else:
            response_context = urllib.request.urlopen(
                request, timeout=timeout_seconds, context=context
            )
        with response_context as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raise VerificationError(f"{method} {url} returned HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VerificationError(
            f"{method} {url} could not be reached: {type(exc).__name__}"
        ) from None

    try:
        decoded = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise VerificationError(f"{method} {url} returned a non-JSON response") from None
    return status, decoded


def submit_prediction(base_url: str) -> str:
    """Submit one synthetic prediction and return its model version."""
    status, payload = request_json(
        f"{base_url}/predict",
        method="POST",
        body={"title": SYNTHETIC_TITLE, "description": SYNTHETIC_DESCRIPTION},
    )
    if status != 200:
        raise VerificationError(f"POST /predict returned HTTP {status}")
    if not isinstance(payload, dict):
        raise VerificationError("POST /predict did not return a JSON object")

    priority = payload.get("priority")
    model_version = payload.get("model_version")
    if not isinstance(priority, str) or priority not in ALLOWED_PRIORITIES:
        raise VerificationError("POST /predict returned an unsupported priority class")
    if not isinstance(model_version, str) or not model_version.strip():
        raise VerificationError("POST /predict returned no model version")

    print(f"response_priority={priority}")
    print(f"response_model_version={model_version}")
    return model_version


def resolve_production_version(tracking_uri: str, model_name: str) -> str:
    """Return the concrete version currently referenced by @production."""
    from mlflow.exceptions import MlflowException
    from mlflow.tracking import MlflowClient

    client = MlflowClient(tracking_uri=tracking_uri)
    try:
        version = client.get_model_version_by_alias(model_name, PRODUCTION_ALIAS)
    except MlflowException as exc:
        raise VerificationError(
            f"could not resolve the {PRODUCTION_ALIAS!r} alias: {type(exc).__name__}"
        ) from None

    concrete = str(getattr(version, "version", "")).strip()
    if not concrete.isdigit() or int(concrete) < 1:
        raise VerificationError(
            f"the {PRODUCTION_ALIAS!r} alias has no valid concrete version"
        )
    print(f"production_alias_version={concrete}")
    return concrete


def run() -> None:
    serving_base_url = os.environ.get("SERVING_BASE_URL", "").strip().rstrip("/")
    expected_version = os.environ.get("EXPECTED_MODEL_VERSION", "").strip()
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "").strip()
    model_name = os.environ.get("MODEL_NAME", DEFAULT_MODEL_NAME).strip()

    if not serving_base_url:
        raise VerificationError("SERVING_BASE_URL is required")
    if not serving_base_url.startswith(("http://", "https://")):
        raise VerificationError("SERVING_BASE_URL must be an http or https URL")
    if not expected_version.isdigit() or int(expected_version) < 1:
        raise VerificationError("EXPECTED_MODEL_VERSION must be a positive integer")
    if not tracking_uri:
        raise VerificationError("MLFLOW_TRACKING_URI is required")
    if not model_name:
        raise VerificationError("MODEL_NAME must not be empty")

    alias_version = resolve_production_version(tracking_uri, model_name)
    if alias_version != expected_version:
        raise VerificationError(
            "the @production alias does not match the approved version "
            f"(alias={alias_version}, expected={expected_version})"
        )

    response_version = submit_prediction(serving_base_url)
    if response_version != expected_version:
        raise VerificationError(
            "the public prediction does not match the @production alias "
            f"(response={response_version}, expected={expected_version})"
        )
    print("production_verify=passed")


def main() -> int:
    try:
        run()
    except VerificationError as exc:
        print(f"production verify failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"production verify failed: unexpected {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
