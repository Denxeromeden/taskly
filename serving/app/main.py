"""FastAPI contract for serving the Taskly priority classifier."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal, Protocol

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, field_validator
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

PRIORITIES = frozenset({"low", "medium", "high"})
UNLOADED_MODEL_VERSION = "unloaded"
logger = logging.getLogger(__name__)


class Predictor(Protocol):
    def predict(self, inputs: list[str]) -> Any:
        """Return one prediction for each supplied input."""


class PredictionRequest(BaseModel):
    """Validated input for a single task-priority prediction."""

    model_config = ConfigDict(extra="ignore")

    title: str
    description: str = ""

    @field_validator("title")
    @classmethod
    def title_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must contain non-whitespace text")
        return value


class PredictionResponse(BaseModel):
    priority: Literal["low", "medium", "high"]
    model_version: str


@dataclass
class PredictionMetrics:
    registry: CollectorRegistry
    requests: Counter
    duration: Histogram
    model_info: Gauge


def _create_metrics() -> PredictionMetrics:
    registry = CollectorRegistry()
    requests = Counter(
        "taskly_prediction_requests",
        "Number of prediction requests handled by the serving API.",
        labelnames=("model_version", "outcome"),
        registry=registry,
    )
    duration = Histogram(
        "taskly_prediction_request_duration_seconds",
        "Time spent handling prediction requests in seconds.",
        labelnames=("model_version",),
        registry=registry,
    )
    model_info = Gauge(
        "taskly_model_info",
        "Information about the model currently loaded by the serving API.",
        labelnames=("model_version",),
        registry=registry,
    )
    return PredictionMetrics(
        registry=registry,
        requests=requests,
        duration=duration,
        model_info=model_info,
    )


def set_loaded_model(app: FastAPI, model: Predictor, model_version: str) -> None:
    """Publish a loaded model and its immutable registry version to the app."""
    version = str(model_version).strip()
    if not version:
        raise ValueError("model_version must not be empty")

    previous_version = getattr(app.state, "model_version", None)
    metrics: PredictionMetrics = app.state.metrics
    if previous_version:
        metrics.model_info.labels(model_version=str(previous_version)).set(0)

    app.state.model = model
    app.state.model_version = version
    metrics.model_info.labels(model_version=version).set(1)


def create_app(
    model: Predictor | None = None,
    model_version: str | None = None,
) -> FastAPI:
    """Create the API; model loading is supplied by the startup layer."""
    app = FastAPI(title="Taskly Priority Serving API")
    app.state.model = None
    app.state.model_version = None
    app.state.metrics = _create_metrics()

    if model is not None:
        if model_version is None:
            raise ValueError("model_version is required when a model is supplied")
        set_loaded_model(app, model, model_version)

    @app.middleware("http")
    async def record_prediction_metrics(request: Request, call_next: Any) -> Response:
        if request.method != "POST" or request.url.path != "/predict":
            return await call_next(request)

        version = request.app.state.model_version or UNLOADED_MODEL_VERSION
        started = perf_counter()
        outcome = "error"
        try:
            response = await call_next(request)
            outcome = "success" if response.status_code < 400 else "error"
            return response
        finally:
            elapsed = perf_counter() - started
            metrics: PredictionMetrics = request.app.state.metrics
            metrics.requests.labels(model_version=version, outcome=outcome).inc()
            metrics.duration.labels(model_version=version).observe(elapsed)

    @app.post("/predict", response_model=PredictionResponse)
    def predict(payload: PredictionRequest, request: Request) -> PredictionResponse:
        predictor: Predictor | None = request.app.state.model
        model_version = request.app.state.model_version
        if predictor is None or model_version is None:
            raise HTTPException(status_code=503, detail="Model is not ready")

        model_input = payload.title + " " + payload.description
        try:
            predictions = predictor.predict([model_input])
            if len(predictions) != 1:
                raise ValueError("model must return exactly one prediction")
            priority = predictions[0]
            if not isinstance(priority, str) or priority not in PRIORITIES:
                raise ValueError("model returned an unsupported priority")
        except Exception as exc:
            logger.error(
                json.dumps(
                    {
                        "event": "prediction_failed",
                        "model_version": str(model_version),
                        "error_type": type(exc).__name__,
                    },
                    separators=(",", ":"),
                )
            )
            raise HTTPException(
                status_code=500,
                detail="Prediction failed",
            ) from exc

        return PredictionResponse(
            priority=priority,
            model_version=str(model_version),
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def readiness(request: Request) -> dict[str, str]:
        if request.app.state.model is None or request.app.state.model_version is None:
            raise HTTPException(status_code=503, detail="Model is not ready")
        return {"status": "ready"}

    @app.get("/metrics")
    async def metrics_endpoint(request: Request) -> Response:
        metrics: PredictionMetrics = request.app.state.metrics
        return Response(
            content=generate_latest(metrics.registry),
            media_type=CONTENT_TYPE_LATEST,
        )

    return app


app = create_app()
