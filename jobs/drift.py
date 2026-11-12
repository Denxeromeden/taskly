"""Compare recent labeled tasks with a bounded reference window and publish drift results."""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

LOGGER = logging.getLogger("taskly.drift")
MODEL_NAME = "task-priority-classifier"
MODEL_ALIAS = "production"
CURRENT_WINDOW_SIZE = 200
MINIMUM_WINDOW_SIZE = 50
REFERENCE_WINDOW_LIMIT = 2_000
DRIFT_SHARE_THRESHOLD = 0.30
FEATURE_COLUMNS = (
    "title_characters",
    "title_words",
    "description_words",
    "combined_characters",
    "priority",
    "predicted_priority",
)
METRIC_NAMES = (
    "taskly_drift_detected",
    "taskly_drift_score",
    "taskly_drift_last_run_timestamp_seconds",
    "taskly_drift_last_success_timestamp_seconds",
    "taskly_drift_reference_rows",
    "taskly_drift_current_rows",
    "taskly_drift_report_upload_success",
    "taskly_drift_run_status",
)
RUN_STATUS_SUCCESS = 0
RUN_STATUS_SKIPPED_INSUFFICIENT_DATA = 1
RUN_STATUS_SKIPPED_NO_PRODUCTION_ALIAS = 2
RUN_STATUS_ERROR = 3


@dataclass(frozen=True)
class Windows:
    reference: tuple[Mapping[str, Any], ...]
    current: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class DriftResult:
    detected: int
    score: float
    reference_rows: int
    current_rows: int
    model_version: str
    report_key: str


class DriftSkipped(RuntimeError):
    def __init__(self, status: int, message: str, reference_rows: int = 0, current_rows: int = 0):
        super().__init__(message)
        self.status = status
        self.reference_rows = reference_rows
        self.current_rows = current_rows


def select_windows(rows: Sequence[Mapping[str, Any]]) -> Windows:
    """Select the newest 200 rows and up to 2,000 preceding labeled rows."""
    ordered = sorted(rows, key=lambda row: int(row["id"]))
    current = tuple(ordered[-CURRENT_WINDOW_SIZE:])
    reference = tuple(ordered[max(0, len(ordered) - CURRENT_WINDOW_SIZE - REFERENCE_WINDOW_LIMIT):-CURRENT_WINDOW_SIZE])
    return Windows(reference=reference, current=current)


def build_feature_rows(
    rows: Sequence[Mapping[str, Any]], predictions: Sequence[Any]
) -> list[dict[str, Any]]:
    """Return aggregate-safe feature columns; never include text or row identifiers."""
    if len(rows) != len(predictions):
        raise ValueError("prediction count does not match data row count")

    result: list[dict[str, Any]] = []
    for row, prediction in zip(rows, predictions):
        title = str(row.get("title") or "")
        description = str(row.get("description") or "")
        result.append(
            {
                "title_characters": len(title),
                "title_words": len(title.split()),
                "description_words": len(description.split()),
                "combined_characters": len(f"{title} {description}".strip()),
                "priority": str(row["priority"]),
                "predicted_priority": str(prediction),
            }
        )
    return result


def parse_existing_metrics(payload: str) -> dict[str, float]:
    """Read the stable Pushgateway group so skipped runs preserve last success."""
    values: dict[str, float] = {}
    for line in payload.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^(taskly_drift_[a-z_]+)(?:\{([^}]*)\})?\s+([-+0-9.eE]+)$", line)
        if match:
            labels = match.group(2) or ""
            if not re.search(r'(?:^|,)\s*job="taskly-drift-detection"(?:,|$)', labels):
                continue
            try:
                values[match.group(1)] = float(match.group(3))
            except ValueError:
                continue
    return values


def metric_payload(values: Mapping[str, float]) -> bytes:
    help_text = {
        "taskly_drift_detected": "Whether a valid drift comparison exceeded the configured drift-share threshold.",
        "taskly_drift_score": "Share of monitored features flagged as drifted by Evidently.",
        "taskly_drift_last_run_timestamp_seconds": "Unix timestamp of the most recent drift attempt.",
        "taskly_drift_last_success_timestamp_seconds": "Unix timestamp of the most recent comparison and report upload.",
        "taskly_drift_reference_rows": "Number of labeled reference rows in the last valid comparison.",
        "taskly_drift_current_rows": "Number of labeled current rows in the last valid comparison.",
        "taskly_drift_report_upload_success": "Whether the report upload for the last attempt succeeded.",
        "taskly_drift_run_status": "Latest run status: 0 success, 1 insufficient data, 2 no production alias, 3 error.",
    }
    lines: list[str] = []
    for name in METRIC_NAMES:
        lines.append(f"# HELP {name} {help_text[name]}")
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {float(values.get(name, 0.0)):.10g}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _push_metrics(pushgateway_url: str, values: Mapping[str, float]) -> None:
    url = pushgateway_url.rstrip("/") + "/metrics/job/taskly-drift-detection"
    request = Request(
        url,
        data=metric_payload(values),
        headers={"Content-Type": "text/plain; version=0.0.4; charset=utf-8"},
        method="PUT",
    )
    with urlopen(request, timeout=10) as response:
        if response.status not in (200, 202):
            raise RuntimeError(f"Pushgateway returned HTTP {response.status}")


def _read_previous_metrics(pushgateway_url: str) -> dict[str, float]:
    # Pushgateway exposes pushed metrics for scraping at /metrics. Its
    # /metrics/job/<group> endpoint is for PUT/POST/DELETE, not GET.
    url = pushgateway_url.rstrip("/") + "/metrics"
    try:
        with urlopen(Request(url, method="GET"), timeout=5) as response:
            return parse_existing_metrics(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return {}
        raise RuntimeError(f"Pushgateway read failed with HTTP {exc.code}") from None
    except URLError:
        raise RuntimeError("Pushgateway is unavailable") from None


def _database_rows(database_url: str) -> list[dict[str, Any]]:
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                result = connection.execute(
                    text(
                        "SELECT id, title, description, priority "
                        "FROM public.tasks "
                        "WHERE priority IS NOT NULL "
                        "ORDER BY id DESC "
                        "LIMIT :limit"
                    ),
                    {"limit": CURRENT_WINDOW_SIZE + REFERENCE_WINDOW_LIMIT},
                )
                rows = [dict(row) for row in result.mappings().all()]
        return list(reversed(rows))
    except Exception as exc:
        raise RuntimeError(f"database read failed ({type(exc).__name__})") from None
    finally:
        engine.dispose()


def _production_version(tracking_uri: str, model_name: str) -> str | None:
    from mlflow.tracking import MlflowClient
    from mlflow.exceptions import MlflowException

    client = MlflowClient(tracking_uri=tracking_uri)
    try:
        version = client.get_model_version_by_alias(model_name, MODEL_ALIAS)
    except MlflowException as exc:
        code = getattr(exc, "error_code", None)
        if code is None:
            code = getattr(exc, "get_error_code", lambda: None)()
        code = getattr(code, "name", code)
        message = str(exc).lower()
        if str(code).endswith("RESOURCE_DOES_NOT_EXIST"):
            return None
        if str(code).endswith("INVALID_PARAMETER_VALUE") and "alias" in message and "not found" in message:
            return None
        raise RuntimeError(f"MLflow alias lookup failed ({type(exc).__name__})") from None
    version_number = str(getattr(version, "version", "")).strip()
    if not version_number.isdigit():
        raise RuntimeError("MLflow returned an invalid production model version")
    return version_number


def _analyze(
    windows: Windows, tracking_uri: str, model_name: str, model_version: str
) -> tuple[float, str]:
    import mlflow
    import pandas as pd
    from evidently import Report
    from evidently.presets import DataDriftPreset

    model_uri = f"models:/{model_name}/{model_version}"
    model = mlflow.pyfunc.load_model(model_uri)
    all_rows = list(windows.reference) + list(windows.current)
    inputs = [
        f"{str(row.get('title') or '')} {str(row.get('description') or '')}".strip()
        for row in all_rows
    ]
    predictions = list(model.predict(inputs))
    all_features = build_feature_rows(all_rows, predictions)
    split = len(windows.reference)
    reference = pd.DataFrame(all_features[:split], columns=FEATURE_COLUMNS)
    current = pd.DataFrame(all_features[split:], columns=FEATURE_COLUMNS)

    report = Report(
        [
            DataDriftPreset(
                columns=list(FEATURE_COLUMNS),
                method="psi",
                threshold=0.1,
                drift_share=DRIFT_SHARE_THRESHOLD,
                include_tests=False,
            )
        ]
    )
    snapshot = report.run(current, reference)
    score = None
    for result in snapshot.metric_results.values():
        share = getattr(result, "share", None)
        if share is not None:
            score = float(share.value)
            break
    if score is None or not 0.0 <= score <= 1.0:
        raise RuntimeError("Evidently did not return a valid drift score")
    return score, snapshot.get_html_str(as_iframe=False)


def _upload_report(bucket: str, region: str, job_name: str, html: str) -> str:
    import boto3

    now = datetime.now(timezone.utc)
    safe_job_name = re.sub(r"[^a-zA-Z0-9._-]", "-", job_name)[:63] or "manual"
    key = PurePosixPath(
        "drift-reports",
        now.strftime("%Y"),
        now.strftime("%m"),
        now.strftime("%d"),
        safe_job_name,
        "report.html",
    ).as_posix()
    client = boto3.client("s3", region_name=region)
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=html.encode("utf-8"),
        ContentType="text/html; charset=utf-8",
    )
    return key


def _required_environment() -> dict[str, str]:
    required = (
        "TASKS_DB_HOST",
        "TASKS_DB_PORT",
        "TASKS_DB_NAME",
        "TASKS_DB_USERNAME",
        "TASKS_DB_PASSWORD",
        "MLFLOW_TRACKING_URI",
        "ARTIFACTS_BUCKET",
        "AWS_REGION",
        "PUSHGATEWAY_URL",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("missing required environment: " + ", ".join(missing))
    return {name: os.environ[name] for name in required}


def run() -> int:
    from jobs.entrypoint import build_database_url

    pushgateway_url = os.environ.get("PUSHGATEWAY_URL", "").strip()
    if not pushgateway_url:
        raise RuntimeError("PUSHGATEWAY_URL is required")
    previous = _read_previous_metrics(pushgateway_url)
    now = time.time()
    values = {name: previous.get(name, 0.0) for name in METRIC_NAMES}
    values["taskly_drift_last_run_timestamp_seconds"] = now
    values["taskly_drift_run_status"] = RUN_STATUS_ERROR

    try:
        environment = _required_environment()
        database_url = build_database_url(environment)
        rows = _database_rows(database_url)
        windows = select_windows(rows)
        reference_count = len(windows.reference)
        current_count = len(windows.current)

        tracking_uri = environment["MLFLOW_TRACKING_URI"]
        model_name = os.environ.get("MODEL_NAME", MODEL_NAME).strip()
        version = _production_version(tracking_uri, model_name)
        if version is None:
            raise DriftSkipped(
                RUN_STATUS_SKIPPED_NO_PRODUCTION_ALIAS,
                "production model alias is not initialized",
                reference_count,
                current_count,
            )
        if reference_count < MINIMUM_WINDOW_SIZE or current_count < MINIMUM_WINDOW_SIZE:
            raise DriftSkipped(
                RUN_STATUS_SKIPPED_INSUFFICIENT_DATA,
                "not enough labeled rows for a comparison",
                reference_count,
                current_count,
            )

        score, html = _analyze(windows, tracking_uri, model_name, version)
        detected = int(score >= DRIFT_SHARE_THRESHOLD)
        job_name = os.environ.get("JOB_NAME", "manual")
        try:
            key = _upload_report(
                environment["ARTIFACTS_BUCKET"],
                environment["AWS_REGION"],
                job_name,
                html,
            )
            upload_success = 1.0
        except Exception as exc:
            LOGGER.error("drift report upload failed: %s", type(exc).__name__)
            upload_success = 0.0
            values.update(
                {
                    "taskly_drift_detected": float(detected),
                    "taskly_drift_score": score,
                    "taskly_drift_reference_rows": float(reference_count),
                    "taskly_drift_current_rows": float(current_count),
                    "taskly_drift_report_upload_success": upload_success,
                    "taskly_drift_run_status": RUN_STATUS_ERROR,
                }
            )
            _push_metrics(pushgateway_url, values)
            return 1

        values.update(
            {
                "taskly_drift_detected": float(detected),
                "taskly_drift_score": score,
                "taskly_drift_last_success_timestamp_seconds": time.time(),
                "taskly_drift_reference_rows": float(reference_count),
                "taskly_drift_current_rows": float(current_count),
                "taskly_drift_report_upload_success": upload_success,
                "taskly_drift_run_status": RUN_STATUS_SUCCESS,
            }
        )
        _push_metrics(pushgateway_url, values)
        LOGGER.info(
            json.dumps(
                {
                    "event": "drift_comparison_completed",
                    "reference_rows": reference_count,
                    "current_rows": current_count,
                    "model_version": version,
                    "drift_score": round(score, 6),
                    "drift_detected": bool(detected),
                    "report_key": key,
                },
                separators=(",", ":"),
            )
        )
        return 0
    except DriftSkipped as exc:
        values["taskly_drift_run_status"] = float(exc.status)
        values["taskly_drift_reference_rows"] = float(exc.reference_rows)
        values["taskly_drift_current_rows"] = float(exc.current_rows)
        values["taskly_drift_report_upload_success"] = 0.0
        _push_metrics(pushgateway_url, values)
        LOGGER.info(
            json.dumps(
                {
                    "event": "drift_comparison_skipped",
                    "status": exc.status,
                    "reason": str(exc),
                    "reference_rows": exc.reference_rows,
                    "current_rows": exc.current_rows,
                },
                separators=(",", ":"),
            )
        )
        return 0
    except Exception as exc:
        values["taskly_drift_run_status"] = RUN_STATUS_ERROR
        values["taskly_drift_report_upload_success"] = 0.0
        try:
            _push_metrics(pushgateway_url, values)
        except Exception as push_exc:
            LOGGER.error("failed to publish drift failure metrics: %s", type(push_exc).__name__)
        LOGGER.error("drift run failed (%s)", type(exc).__name__)
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    raise SystemExit(run())
