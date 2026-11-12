import ssl
import sys
from types import ModuleType, SimpleNamespace

import pytest

from jobs import verify_production
from jobs.verify_production import (
    VerificationError,
    main,
    request_json,
    resolve_production_version,
    run,
    submit_prediction,
)


def test_submit_prediction_returns_the_response_model_version(monkeypatch):
    calls = []

    def fake_request_json(url, method="GET", body=None, timeout_seconds=10.0):
        calls.append((url, method, body))
        return 200, {"priority": "high", "model_version": "5"}

    monkeypatch.setattr(verify_production, "request_json", fake_request_json)

    version = submit_prediction("https://serving.example.com")

    assert version == "5"
    assert calls == [
        (
            "https://serving.example.com/predict",
            "POST",
            {
                "title": verify_production.SYNTHETIC_TITLE,
                "description": verify_production.SYNTHETIC_DESCRIPTION,
            },
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"priority": "urgent", "model_version": "5"},
        {"priority": "high"},
        {"priority": "high", "model_version": ""},
        {"model_version": "5"},
    ],
)
def test_submit_prediction_rejects_invalid_response_payloads(monkeypatch, payload):
    monkeypatch.setattr(
        verify_production,
        "request_json",
        lambda *args, **kwargs: (200, payload),
    )

    with pytest.raises(VerificationError):
        submit_prediction("https://serving.example.com")


def test_submit_prediction_rejects_non_200_response(monkeypatch):
    monkeypatch.setattr(
        verify_production,
        "request_json",
        lambda *args, **kwargs: (503, {"model_version": "5"}),
    )

    with pytest.raises(VerificationError, match="HTTP 503"):
        submit_prediction("https://serving.example.com")


def test_resolve_production_version_returns_the_concrete_alias_version(monkeypatch):
    class MlflowException(Exception):
        pass

    class StubClient:
        def __init__(self, tracking_uri):
            assert tracking_uri == "http://mlflow.test:5000"

        def get_model_version_by_alias(self, name, alias):
            assert name == "task-priority-classifier"
            assert alias == "production"
            return SimpleNamespace(version="7")

    exceptions = ModuleType("mlflow.exceptions")
    exceptions.MlflowException = MlflowException
    tracking = ModuleType("mlflow.tracking")
    tracking.MlflowClient = StubClient
    mlflow = ModuleType("mlflow")
    mlflow.__path__ = []
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.exceptions", exceptions)
    monkeypatch.setitem(sys.modules, "mlflow.tracking", tracking)

    assert (
        resolve_production_version(
            "http://mlflow.test:5000", "task-priority-classifier"
        )
        == "7"
    )


def test_resolve_production_version_fails_when_alias_is_missing(monkeypatch):
    class MlflowException(Exception):
        pass

    class StubClient:
        def __init__(self, tracking_uri):
            pass

        def get_model_version_by_alias(self, name, alias):
            raise MlflowException("missing alias")

    exceptions = ModuleType("mlflow.exceptions")
    exceptions.MlflowException = MlflowException
    tracking = ModuleType("mlflow.tracking")
    tracking.MlflowClient = StubClient
    mlflow = ModuleType("mlflow")
    mlflow.__path__ = []
    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.exceptions", exceptions)
    monkeypatch.setitem(sys.modules, "mlflow.tracking", tracking)

    with pytest.raises(VerificationError, match="could not resolve"):
        resolve_production_version(
            "http://mlflow.test:5000", "task-priority-classifier"
        )


def set_run_environment(monkeypatch):
    monkeypatch.setenv("SERVING_BASE_URL", "https://serving.example.com/")
    monkeypatch.setenv("EXPECTED_MODEL_VERSION", "5")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.test:5000")
    monkeypatch.setenv("MODEL_NAME", "task-priority-classifier")


def test_run_passes_when_alias_and_public_response_match(monkeypatch):
    set_run_environment(monkeypatch)
    monkeypatch.setattr(verify_production, "resolve_production_version", lambda *args: "5")
    monkeypatch.setattr(verify_production, "submit_prediction", lambda base_url: "5")

    run()


def test_run_rejects_alias_mismatch(monkeypatch):
    set_run_environment(monkeypatch)
    monkeypatch.setattr(verify_production, "resolve_production_version", lambda *args: "6")
    monkeypatch.setattr(verify_production, "submit_prediction", lambda base_url: "6")

    with pytest.raises(VerificationError, match="@production alias"):
        run()


def test_run_rejects_public_response_mismatch(monkeypatch):
    set_run_environment(monkeypatch)
    monkeypatch.setattr(verify_production, "resolve_production_version", lambda *args: "5")
    monkeypatch.setattr(verify_production, "submit_prediction", lambda base_url: "6")

    with pytest.raises(VerificationError, match="public prediction"):
        run()


@pytest.mark.parametrize("expected_version", ["", "0", "abc"])
def test_run_rejects_invalid_expected_version(monkeypatch, expected_version):
    set_run_environment(monkeypatch)
    monkeypatch.setenv("EXPECTED_MODEL_VERSION", expected_version)

    with pytest.raises(VerificationError, match="EXPECTED_MODEL_VERSION"):
        run()


def test_tls_verification_can_be_disabled_for_self_signed_showcase(monkeypatch):
    monkeypatch.setenv("TLS_VERIFY", "false")

    context = verify_production._tls_context()

    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE


def test_tls_verification_is_enabled_by_default(monkeypatch):
    monkeypatch.delenv("TLS_VERIFY", raising=False)

    context = verify_production._tls_context()

    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_tls_verify_accepts_only_boolean_values(monkeypatch):
    monkeypatch.setenv("TLS_VERIFY", "maybe")

    with pytest.raises(VerificationError, match="TLS_VERIFY"):
        verify_production._tls_context()


def test_main_requires_serving_base_url(monkeypatch, capsys):
    monkeypatch.delenv("SERVING_BASE_URL", raising=False)

    assert main() == 1
    assert "SERVING_BASE_URL is required" in capsys.readouterr().err


def test_request_json_uses_an_unverified_context_for_https(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(request, timeout, context=None):
        captured["context"] = context
        return Response()

    monkeypatch.setenv("TLS_VERIFY", "false")
    monkeypatch.setattr(verify_production.urllib.request, "urlopen", fake_urlopen)

    status, payload = request_json("https://serving.example.com/health")

    assert status == 200
    assert payload == {"ok": True}
    assert captured["context"].verify_mode == ssl.CERT_NONE
