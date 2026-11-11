"""Small standard-library JSON logging setup for the serving API."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JsonLogFormatter(logging.Formatter):
    """Render application events as one JSON object per stdout line."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            payload: dict[str, Any] = json.loads(record.getMessage())
            if not isinstance(payload, dict):
                raise ValueError("structured log message must be a JSON object")
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {"event": record.getMessage()}

        payload.setdefault("event", record.getMessage())
        payload.setdefault("model_version", "unloaded")
        payload.setdefault(
            "timestamp",
            datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        )
        payload.setdefault("level", record.levelname)
        payload.setdefault("logger", record.name)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> None:
    """Attach one JSON stdout handler to the serving logger hierarchy."""
    serving_logger = logging.getLogger("serving")
    serving_logger.setLevel(logging.INFO)

    if not any(getattr(handler, "_taskly_json_handler", False) for handler in serving_logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonLogFormatter())
        handler._taskly_json_handler = True  # type: ignore[attr-defined]
        serving_logger.addHandler(handler)

    # Propagation keeps records available to pytest's caplog handler as well.
    serving_logger.propagate = True
