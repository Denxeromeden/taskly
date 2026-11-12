"""Compare the newest registered model with production and update staging."""

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
    """Raised when promotion cannot safely make a decision."""


@dataclass(frozen=True)
class PromotionResult:
    decision: str
    candidate_version: str
    candidate_metric: float | None
    production_version: str | None
    production_metric: float | None
    registered_versions: tuple[str, ...]


def _version_number(model_version: Any) -> int:
    try:
        return int(model_version.version)
    except (AttributeError, TypeError, ValueError) as exc:
        raise PromotionError("A registered model version has an invalid version number") from exc


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
    # Only this specific message is safe to interpret as the first-run case.
    if normalized_code == "INVALID_PARAMETER_VALUE":
        expected_message = f"registered model alias {alias.lower()} not found"
        return expected_message in str(error).lower()

    return False


def _metric_for_version(client: Any, model_version: Any) -> float | None:
    run_id = getattr(model_version, "run_id", None)
    if not run_id:
        return None
    return _numeric_metric(client.get_run(run_id))


def _log_result(log: Callable[[str], None], result: PromotionResult) -> None:
    production_version = result.production_version or "none"
    candidate_metric = (
        f"{result.candidate_metric:.6f}" if result.candidate_metric is not None else "missing"
    )
    production_metric = (
        f"{result.production_metric:.6f}" if result.production_metric is not None else "missing"
    )
    log(
        "promotion result: "
        f"candidate_version={result.candidate_version} "
        f"candidate_f1_macro={candidate_metric} "
        f"production_version={production_version} "
        f"production_f1_macro={production_metric} "
        f"decision={result.decision}"
    )


def promote_latest_model(
    client: Any,
    model_name: str = DEFAULT_MODEL_NAME,
    log: Callable[[str], None] = print,
) -> PromotionResult:
    """Compare the newest version and update only the staging alias when safe."""
    if not model_name.strip():
        raise PromotionError("MODEL_NAME must not be empty")

    versions = list(client.search_model_versions(filter_string=f"name = '{model_name}'"))
    if not versions:
        raise PromotionError(f"No registered versions found for model {model_name!r}")

    ordered_versions = sorted(versions, key=_version_number)
    version_numbers = tuple(str(version.version) for version in ordered_versions)
    log(f"registered versions found for {model_name}: {', '.join(version_numbers)}")

    candidate = ordered_versions[-1]
    candidate_version = str(candidate.version)
    candidate_metric = _metric_for_version(client, candidate)

    try:
        production = client.get_model_version_by_alias(model_name, PRODUCTION_ALIAS)
    except Exception as exc:
        if not _is_missing_alias_error(exc, PRODUCTION_ALIAS):
            raise
        production = None

    production_version = str(production.version) if production is not None else None
    production_metric = (
        _metric_for_version(client, production) if production is not None else None
    )

    if candidate_metric is None or (
        production is not None and production_metric is None
    ):
        result = PromotionResult(
            decision="FAIL_CLOSED_MISSING_METRIC",
            candidate_version=candidate_version,
            candidate_metric=candidate_metric,
            production_version=production_version,
            production_metric=production_metric,
            registered_versions=version_numbers,
        )
        _log_result(log, result)
        raise PromotionError(
            f"Missing or non-numeric {PROMOTION_METRIC} metric; no model alias was changed"
        )

    if production is None:
        decision = "INITIAL_STAGING_CANDIDATE"
        client.set_registered_model_alias(model_name, STAGING_ALIAS, candidate_version)
    elif candidate_metric >= production_metric:
        decision = "PROMOTED_TO_STAGING"
        client.set_registered_model_alias(model_name, STAGING_ALIAS, candidate_version)
    else:
        decision = "REJECTED_METRIC_REGRESSION"

    result = PromotionResult(
        decision=decision,
        candidate_version=candidate_version,
        candidate_metric=candidate_metric,
        production_version=production_version,
        production_metric=production_metric,
        registered_versions=version_numbers,
    )
    _log_result(log, result)
    return result


def main() -> int:
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI", "").strip()
    model_name = os.environ.get("MODEL_NAME", DEFAULT_MODEL_NAME).strip()
    if not tracking_uri:
        print("promotion failed: MLFLOW_TRACKING_URI is required", file=sys.stderr)
        return 1
    if not model_name:
        print("promotion failed: MODEL_NAME must not be empty", file=sys.stderr)
        return 1

    try:
        from mlflow.tracking import MlflowClient

        client = MlflowClient(tracking_uri=tracking_uri)
        promote_latest_model(client, model_name=model_name)
    except Exception as exc:
        print(f"promotion failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
