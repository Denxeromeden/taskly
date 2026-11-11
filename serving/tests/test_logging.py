import json
import logging

from fastapi.testclient import TestClient

from serving.app.logging_config import JsonLogFormatter
from serving.app.main import create_app


class StubModel:
    def predict(self, inputs):
        return ["high"]


def test_json_formatter_adds_required_fields_and_unloaded_default():
    record = logging.LogRecord(
        name="serving.app.main",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='{"event":"model_load_started"}',
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["event"] == "model_load_started"
    assert payload["model_version"] == "unloaded"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "serving.app.main"
    assert payload["timestamp"].endswith("Z")


def test_prediction_request_log_has_request_metadata_without_payload(caplog):
    caplog.set_level(logging.INFO, logger="serving")
    app = create_app(model=StubModel(), model_version="23")

    with TestClient(app) as client:
        response = client.post(
            "/predict",
            json={"title": "PRIVATE TITLE", "description": "PRIVATE DESCRIPTION"},
        )

    assert response.status_code == 200
    record = next(
        record
        for record in caplog.records
        if getattr(record, "message", "")
        and '"event":"prediction_request"' in record.message
    )
    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["model_version"] == "23"
    assert payload["method"] == "POST"
    assert payload["path"] == "/predict"
    assert payload["status_code"] == 200
    assert payload["duration_seconds"] >= 0
    assert "PRIVATE TITLE" not in json.dumps(payload)
    assert "PRIVATE DESCRIPTION" not in json.dumps(payload)


def test_startup_and_shutdown_events_include_loaded_model_version(caplog):
    caplog.set_level(logging.INFO, logger="serving")
    app = create_app(model=StubModel(), model_version="31")

    with TestClient(app):
        pass

    events = {
        json.loads(record.message)["event"]: json.loads(record.message)
        for record in caplog.records
        if record.message.startswith("{")
    }
    assert events["application_starting"]["model_version"] == "31"
    assert events["application_stopping"]["model_version"] == "31"
