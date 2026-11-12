"""Promote the exact MLflow @staging version to @production with a metric re-check.

The GitHub lifecycle workflow runs this inside the ml-jobs namespace after the
protected `production` Environment approval. It never selects the newest
registered version: the approved release is bound to whatever @staging points
to at execution time. Missing or non-numeric metrics fail closed, and a second
run that finds @production already on the approved version is a successful
no-op.
"""

from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Mapping


PROMOTION_METRIC = "f1_macro"
STAGING_ALIAS = "staging"
PRODUCTION_ALIAS = "production"
DEFAULT_MODEL_NAME = "task-priority-classifier"


class PromotionError(RuntimeError):
    """Raised when the production alias cannot be moved safely."""


@dataclass(frozen=True)
class ProductionPromotionResult:
    decision: str
    staging_version: str
    production_version: str | None
    staging_metric: float | None
    production_metric: float | None


def _version_string(model_version: Any) -> str:
    version = str(getattr(model_version, "version", "")).strip()
    if not version.isdigit():
        raise PromotionError("A registered model version has an invalid version number")
    return version


def _numeric_metric(run: Any) -> float | None:
    metrics = getattr(getattr(run, "data", None), "metrics", None)
    if not isinstance(metrics, Mapping):
        return None

    value = metrics.get(PROMOTION_METRIC)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    try:
        numeric_value = float(value)
    except (OverflowError, ValueError):
        return None
    return numeric_value if math.isfinite(numeric_value) else None


def _metric_for_version(client: Any, model_version: Any) -> float | None:
    run_id = getattr(model_version, "run_id", None)
    if not run_id:
        return None
    return _numeric_metric(client.get_run(run_id))


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

    # Older MLflow responses use RESOURCE_DOES_NOT_EXIST for a missing alias.
    if normalized_code == "RESOURCE_DOES_NOT_EXIST":
        return True

    # Some MLflow versions report a missing alias as INVALID_PARAMETER_VALUE.
    # Only this specific message is safe to interpret as an absent alias.
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


def _log_result(log: Callable[[str], None], result: ProductionPromotionResult) -> None:
    production_version = result.production_version or "none"
    staging_metric = (
        f"{result.staging_metric:.6f}" if result.staging_metric is not None else "missing"
    )
    production_metric = (
        f"{result.production_metric:.6f}" if result.production_metric is not None else "missing"
    )
    log(
        "production promotion result: "
        f"staging_version={result.staging_version} "
        f"staging_f1_macro={staging_metric} "
        f"production_version={production_version} "
        f"production_f1_macro={production_metric} "
        f"decision={result.decision}"
    )


def promote_staging_to_production(
    client: Any,
    model_name: str = DEFAULT_MODEL_NAME,
    log: Callable[[str], None] = print,
) -> ProductionPromotionResult:
    """Move @production to the exact version currently behind @staging."""
    if not model_name.strip():
        raise PromotionError("MODEL_NAME must not be empty")

    staging = _resolve_alias(client, model_name, STAGING_ALIAS)
    if staging is None:
        raise PromotionError(
            f"No @{STAGING_ALIAS} model version is available to promote to @{PRODUCTION_ALIAS}"
        )

    production = _resolve_alias(client, model_name, PRODUCTION_ALIAS)

    staging_version = _version_string(staging)
    production_version = _version_string(production) if production is not None else None

    staging_metric = _metric_for_version(client, staging)
    production_metric = (
        _metric_for_version(client, production) if production is not None else None
    )

    if staging_metric is None or (
        production is not None and production_metric is None
    ):
        result = ProductionPromotionResult(
            decision="FAIL_CLOSED_MISSING_METRIC",
            staging_version=staging_version,
            production_version=production_version,
            staging_metric=staging_metric,
            production_metric=production_metric,
        )
        _log_result(log, result)
        raise PromotionError(
            f"Missing or non-numeric {PROMOTION_METRIC} for the staging candidate or "
            "current production version; no model alias was changed"
        )

    if production is None:
        decision = "INITIALIZED_PRODUCTION"
        client.set_registered_model_alias(model_name, PRODUCTION_ALIAS, staging_version)
    elif production_version == staging_version:
        decision = "NOOP_ALREADY_CURRENT"
    elif staging_metric >= production_metric:
        decision = "PROMOTED_TO_PRODUCTION"
        client.set_registered_model_alias(model_name, PRODUCTION_ALIAS, staging_version)
    else:
        decision = "REJECTED_METRIC_REGRESSION"
        result = ProductionPromotionResult(
            decision=decision,
            staging_version=staging_version,
            production_version=production_version,
            staging_metric=staging_metric,
            production_metric=production_metric,
        )
        _log_result(log, result)
        raise PromotionError(
            "The approved staging candidate no longer matches or exceeds the current "
            "production metric; no model alias was changed"
        )

    result = ProductionPromotionResult(
        decision=decision,
        staging_version=staging_version,
        production_version=production_version,
        staging_metric=staging_metric,
        production_metric=production_metric,
    )
    _log_result(log, result)
    return result


def main() -> int:
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "").strip()
    model_name = os.environ.get("MODEL_NAME", DEFAULT_MODEL_NAME).strip()
    if not tracking_uri:
        print("production promotion failed: MLFLOW_TRACKING_URI is required", file=sys.stderr)
        return 1
    if not model_name:
        print("production promotion failed: MODEL_NAME must not be empty", file=sys.stderr)
        return 1

    try:
        from mlflow.tracking import MlflowClient

        client = MlflowClient(tracking_uri=tracking_uri)
        promote_staging_to_production(client, model_name=model_name)
    except Exception as exc:
        print(f"production promotion failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
