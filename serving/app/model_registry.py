"""Resolve and load a concrete model version from the MLflow registry."""

from __future__ import annotations

import os
from typing import Any, Mapping


def _required_setting(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable {name} is not set")
    return value


def load_registered_model(
    environment: Mapping[str, str] | None = None,
) -> tuple[Any, str]:
    """Load the exact registry version currently assigned to the configured alias."""
    source_environment = os.environ if environment is None else environment
    tracking_uri = _required_setting(source_environment, "MLFLOW_TRACKING_URI")
    model_name = _required_setting(source_environment, "MODEL_NAME")
    model_alias = _required_setting(source_environment, "MODEL_ALIAS")

    import mlflow
    from mlflow import MlflowClient

    mlflow.set_tracking_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri)
    registered_version = client.get_model_version_by_alias(
        name=model_name,
        alias=model_alias,
    )

    raw_version = getattr(registered_version, "version", None)
    try:
        version_number = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("MLflow returned an invalid model version") from exc
    if version_number < 1:
        raise RuntimeError("MLflow returned an invalid model version")

    version = str(version_number)
    model_uri = f"models:/{model_name}/{version}"
    model = mlflow.pyfunc.load_model(model_uri)
    return model, version
