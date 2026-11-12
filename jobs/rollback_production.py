"""Move the MLflow @production alias to an explicitly requested version.

The protected production-rollback workflow runs this inside the ml-jobs
namespace after Environment approval. The requested version must already exist
for the configured model; invalid versions fail without moving the alias. The
workflow then restarts the production Deployment and checks the public
prediction response.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Callable


PRODUCTION_ALIAS = "production"
DEFAULT_MODEL_NAME = "task-priority-classifier"


class RollbackError(RuntimeError):
    """Raised when the production alias cannot be moved safely."""


@dataclass(frozen=True)
class RollbackResult:
    requested_version: str
    old_production_version: str | None
    decision: str


def _version_string(value: Any) -> str:
    version = str(value).strip()
    if not version.isdigit() or int(version) < 1:
        raise RollbackError("Model versions must be positive integers")
    return version


def _is_missing_alias_error(error: Exception, alias: str) -> bool:
    """Recognize supported MLflow missing-alias responses only."""
    error_code = getattr(error, "error_code", None)
    if error_code is None or isinstance(error_code, int):
        get_error_code = getattr(error, "get_error_code", None)
        if callable(get_error_code):
            error_code = get_error_code()

    if hasattr(error_code, "name"):
        error_code = error_code.name
    normalized_code = str(error_code).rsplit(".", 1)[-1]

    if normalized_code == "RESOURCE_DOES_NOT_EXIST":
        return True
    if normalized_code == "INVALID_PARAMETER_VALUE":
        expected_message = f"registered model alias {alias.lower()} not found"
        return expected_message in str(error).lower()
    return False


def _resolve_alias(client: Any, model_name: str, alias: str) -> Any | None:
    try:
        return client.get_model_version_by_alias(model_name, alias)
    except Exception as exc:
        if _is_missing_alias_error(exc, alias):
            return None
        raise


def _log_result(log: Callable[[str], None], result: RollbackResult) -> None:
    old_version = result.old_production_version or "none"
    log(
        "production rollback result: "
        f"requested_version={result.requested_version} "
        f"old_production_version={old_version} "
        f"new_production_version={result.requested_version} "
        f"decision={result.decision}"
    )


def rollback_production(
    client: Any,
    model_version: str,
    model_name: str = DEFAULT_MODEL_NAME,
    log: Callable[[str], None] = print,
) -> RollbackResult:
    """Set @production to an existing requested version."""
    if not model_name.strip():
        raise RollbackError("MODEL_NAME must not be empty")

    requested_version = _version_string(model_version)
    try:
        requested = client.get_model_version(model_name, requested_version)
    except Exception:
        raise RollbackError(
            f"Model version {requested_version} does not exist for {model_name!r}; "
            "no model alias was changed"
        ) from None

    if _version_string(getattr(requested, "version", "")) != requested_version:
        raise RollbackError(
            "The registry returned a different model version; no model alias was changed"
        )

    production = _resolve_alias(client, model_name, PRODUCTION_ALIAS)
    old_production_version = (
        _version_string(getattr(production, "version", "")) if production is not None else None
    )

    if old_production_version == requested_version:
        decision = "NOOP_ALREADY_CURRENT"
    else:
        client.set_registered_model_alias(model_name, PRODUCTION_ALIAS, requested_version)
        decision = "ROLLED_BACK"

    result = RollbackResult(
        requested_version=requested_version,
        old_production_version=old_production_version,
        decision=decision,
    )
    _log_result(log, result)
    return result


def main() -> int:
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "").strip()
    model_name = os.environ.get("MODEL_NAME", DEFAULT_MODEL_NAME).strip()
    model_version = os.environ.get("MODEL_VERSION", "").strip()

    if not tracking_uri:
        print("production rollback failed: MLFLOW_TRACKING_URI is required", file=sys.stderr)
        return 1
    if not model_name:
        print("production rollback failed: MODEL_NAME must not be empty", file=sys.stderr)
        return 1
    if not model_version:
        print("production rollback failed: MODEL_VERSION is required", file=sys.stderr)
        return 1

    try:
        from mlflow.tracking import MlflowClient

        client = MlflowClient(tracking_uri=tracking_uri)
        rollback_production(client, model_version=model_version, model_name=model_name)
    except Exception as exc:
        print(f"production rollback failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
