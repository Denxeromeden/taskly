import json

import pytest
from fastapi.testclient import TestClient

from serving.app.main import create_app, set_loaded_model


class StubModel:
    def __init__(self, prediction="high", error=None):
        self.prediction = prediction
        self.error = error
        self.inputs = None

    def predict(self, inputs):
        self.inputs = inputs
        if self.error is not None:
            raise self.error
        return [self.prediction]


def make_client(model=None, version=None):
    app = create_app(model=model, model_version=version)
    return TestClient(app), app


def test_health_is_live_before_model_is_loaded():
    client, _ = make_client()
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_is_unavailable_before_model_is_loaded():
    client, _ = make_client()
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Model is not ready"}


def test_readiness_succeeds_after_model_is_set():
    model = StubModel()
    client, app = make_client()
    set_loaded_model(app, model, 12)
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


@pytest.mark.parametrize("title", ["", "   ", "\t\n"])
def test_predict_rejects_empty_or_whitespace_title(title):
    model = StubModel()
    client, _ = make_client(model, "3")
    response = client.post("/predict", json={"title": title})
    assert response.status_code == 422
    assert model.inputs is None


def test_predict_requires_title():
    client, _ = make_client(StubModel(), "3")
    response = client.post("/predict", json={"description": "No title"})
    assert response.status_code == 422


def test_predict_defaults_optional_description_to_empty_string():
    model = StubModel("medium")
    client, _ = make_client(model, "3")
    response = client.post("/predict", json={"title": "  Payment issue  "})
    assert response.status_code == 200
    assert response.json() == {"priority": "medium", "model_version": "3"}
    assert model.inputs == ["  Payment issue   "]


def test_predict_uses_title_space_description_as_training_input():
    model = StubModel("low")
    client, _ = make_client(model, "7")
    response = client.post(
        "/predict",
        json={"title": "Slow dashboard", "description": "Chart takes too long"},
    )
    assert response.status_code == 200
    assert response.json() == {"priority": "low", "model_version": "7"}
    assert model.inputs == ["Slow dashboard Chart takes too long"]


def test_predict_returns_503_when_model_is_not_ready():
    client, _ = make_client()
    response = client.post("/predict", json={"title": "Checkout fails"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Model is not ready"}


def test_unexpected_model_label_returns_non_sensitive_500(caplog):
    client, _ = make_client(StubModel("urgent"), "9")
    response = client.post("/predict", json={"title": "Checkout fails"})
    assert response.status_code == 500
    assert response.json() == {"detail": "Prediction failed"}
    assert "urgent" not in response.text
    error_records = [r for r in caplog.records if '"event":"prediction_failed"' in r.message]
    event = json.loads(error_records[-1].message)
    assert event == {
        "event": "prediction_failed",
        "model_version": "9",
        "error_type": "ValueError",
    }


def test_inference_exception_returns_non_sensitive_500(caplog):
    client, _ = make_client(StubModel(error=RuntimeError("private detail")), "4")
    response = client.post("/predict", json={"title": "Checkout fails"})
    assert response.status_code == 500
    assert response.json() == {"detail": "Prediction failed"}
    assert "private detail" not in response.text
    error_records = [r for r in caplog.records if '"event":"prediction_failed"' in r.message]
    event = json.loads(error_records[-1].message)
    assert event["event"] == "prediction_failed"
    assert event["model_version"] == "4"
    assert event["error_type"] == "RuntimeError"


def test_metrics_expose_required_names_with_bounded_labels():
    model = StubModel()
    client, _ = make_client(model, "5")
    client.post("/predict", json={"title": "Checkout fails"})
    client.post("/predict", json={"title": "   "})
    response = client.get("/metrics")
    body = response.text
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert 'taskly_prediction_requests_total{model_version="5",outcome="success"} 1.0' in body
    assert 'taskly_prediction_requests_total{model_version="5",outcome="error"} 1.0' in body
    assert 'taskly_prediction_request_duration_seconds_count{model_version="5"} 2.0' in body
    assert 'taskly_model_info{model_version="5"} 1.0' in body
    assert "Checkout fails" not in body


def test_metrics_use_unloaded_version_for_unready_prediction():
    client, _ = make_client()
    client.post("/predict", json={"title": "Checkout fails"})
    body = client.get("/metrics").text
    assert 'taskly_prediction_requests_total{model_version="unloaded",outcome="error"} 1.0' in body
