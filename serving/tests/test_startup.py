import json
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from serving.app import main as serving_main
from serving.app.model_registry import load_registered_model


class StubModel:
    def __init__(self, prediction="high"):
        self.prediction = prediction
        self.inputs = None

    def predict(self, inputs):
        self.inputs = inputs
        return [self.prediction]


def wait_for_readiness(client, expected_status, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get("/ready")
        if response.status_code == expected_status:
            return response
        time.sleep(0.01)
    raise AssertionError(f"/ready did not return HTTP {expected_status} within {timeout}s")


def wait_for_log_event(caplog, event_name, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for record in caplog.records:
            try:
                if json.loads(record.message).get("event") == event_name:
                    return json.loads(record.message)
            except (TypeError, json.JSONDecodeError):
                continue
        time.sleep(0.01)
    raise AssertionError(f"Log event {event_name!r} was not emitted within {timeout}s")


def test_registry_loader_resolves_alias_then_loads_the_concrete_version(monkeypatch):
    calls = {"tracking_uri": None, "client_uri": None, "alias": None, "model_uri": None}
    model = StubModel("medium")

    class FakeMlflowClient:
        def __init__(self, tracking_uri):
            calls["client_uri"] = tracking_uri

        def get_model_version_by_alias(self, name, alias):
            calls["alias"] = (name, alias)
            return SimpleNamespace(version="17")

    fake_mlflow = ModuleType("mlflow")
    fake_mlflow.set_tracking_uri = lambda uri: calls.__setitem__("tracking_uri", uri)
    fake_mlflow.MlflowClient = FakeMlflowClient
    fake_mlflow.pyfunc = SimpleNamespace(
        load_model=lambda uri: calls.__setitem__("model_uri", uri) or model
    )
    monkeypatch.setitem(sys.modules, "mlflow", fake_mlflow)

    loaded_model, version = load_registered_model(
        {
            "MLFLOW_TRACKING_URI": "http://mlflow.mlflow.svc.cluster.local:5000",
            "MODEL_NAME": "task-priority-classifier",
            "MODEL_ALIAS": "staging",
        }
    )

    assert loaded_model is model
    assert version == "17"
    assert calls == {
        "tracking_uri": "http://mlflow.mlflow.svc.cluster.local:5000",
        "client_uri": "http://mlflow.mlflow.svc.cluster.local:5000",
        "alias": ("task-priority-classifier", "staging"),
        "model_uri": "models:/task-priority-classifier/17",
    }


@pytest.mark.parametrize(
    "missing_setting",
    ["MLFLOW_TRACKING_URI", "MODEL_NAME", "MODEL_ALIAS"],
)
def test_registry_loader_requires_all_three_environment_settings(missing_setting):
    environment = {
        "MLFLOW_TRACKING_URI": "http://mlflow:5000",
        "MODEL_NAME": "task-priority-classifier",
        "MODEL_ALIAS": "staging",
    }
    del environment[missing_setting]

    try:
        load_registered_model(environment)
    except RuntimeError as exc:
        assert missing_setting in str(exc)
    else:
        raise AssertionError(f"Missing {missing_setting} should prevent registry loading")


def test_liveness_works_while_model_is_loading_and_readiness_waits(monkeypatch):
    model = StubModel("medium")
    load_started = threading.Event()
    allow_load_to_finish = threading.Event()

    def slow_registry_load():
        load_started.set()
        if not allow_load_to_finish.wait(timeout=2):
            raise RuntimeError("test loader timed out")
        return model, "21"

    monkeypatch.setattr(serving_main, "load_registered_model", slow_registry_load)
    app = serving_main.create_app()

    with TestClient(app) as client:
        assert load_started.wait(timeout=2)
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
        allow_load_to_finish.set()
        assert wait_for_readiness(client, 200).status_code == 200
        prediction = client.post("/predict", json={"title": "Checkout fails"})

    assert prediction.status_code == 200
    assert prediction.json() == {"priority": "medium", "model_version": "21"}
    assert model.inputs == ["Checkout fails "]


def test_startup_failure_keeps_liveness_but_not_readiness(monkeypatch, caplog):
    def fail_loading():
        raise RuntimeError("sensitive registry response")

    monkeypatch.setattr(serving_main, "load_registered_model", fail_loading)
    app = serving_main.create_app()

    with TestClient(app) as client:
        assert wait_for_log_event(caplog, "model_load_failed")["model_version"] == "unloaded"
        health = client.get("/health")
        ready = client.get("/ready")

    assert health.status_code == 200
    assert ready.status_code == 503
    event = wait_for_log_event(caplog, "model_load_failed")
    assert event == {
        "event": "model_load_failed",
        "model_version": "unloaded",
        "error_type": "RuntimeError",
    }
    assert "sensitive registry response" not in caplog.text


def test_injected_model_skips_registry_loading(monkeypatch):
    def unexpected_registry_call():
        raise AssertionError("injected test model must not trigger registry loading")

    monkeypatch.setattr(serving_main, "load_registered_model", unexpected_registry_call)
    app = serving_main.create_app(model=StubModel(), model_version="8")

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
