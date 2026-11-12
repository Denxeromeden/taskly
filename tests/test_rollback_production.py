import sys
from types import ModuleType, SimpleNamespace

import pytest

from jobs.rollback_production import (
    RollbackError,
    main,
    rollback_production,
)


class MlflowMissingAliasError(Exception):
    error_code = "RESOURCE_DOES_NOT_EXIST"


class MlflowInvalidParameterError(Exception):
    error_code = "INVALID_PARAMETER_VALUE"


class StubMlflowClient:
    def __init__(self, versions, production=None):
        self.versions = {str(version): model for version, model in versions.items()}
        self.aliases = {}
        if production is not None:
            self.aliases["production"] = production
        self.alias_updates = []
        self.version_lookups = []

    def get_model_version(self, name, version):
        self.version_lookups.append((name, str(version)))
        if str(version) not in self.versions:
            raise RuntimeError("model version not found")
        return self.versions[str(version)]

    def get_model_version_by_alias(self, name, alias):
        if alias not in self.aliases:
            raise MlflowMissingAliasError(f"{alias} alias does not exist")
        return self.aliases[alias]

    def set_registered_model_alias(self, name, alias, version):
        self.alias_updates.append((name, alias, str(version)))
        self.aliases[alias] = self.versions[str(version)]


def model_version(version):
    return SimpleNamespace(version=str(version))


def test_rollback_moves_production_to_the_requested_existing_version():
    production = model_version(3)
    client = StubMlflowClient(
        versions={3: production, 5: model_version(5)},
        production=production,
    )
    logs = []

    result = rollback_production(client, "5", log=logs.append)

    assert result.decision == "ROLLED_BACK"
    assert result.requested_version == "5"
    assert result.old_production_version == "3"
    assert client.alias_updates == [("task-priority-classifier", "production", "5")]
    assert client.aliases["production"].version == "5"
    assert client.version_lookups == [("task-priority-classifier", "5")]
    assert "old_production_version=3" in logs[-1]
    assert "new_production_version=5" in logs[-1]
    assert "decision=ROLLED_BACK" in logs[-1]


def test_rollback_initializes_missing_production_alias():
    client = StubMlflowClient(versions={4: model_version(4)})
    logs = []

    result = rollback_production(client, "4", log=logs.append)

    assert result.decision == "ROLLED_BACK"
    assert result.old_production_version is None
    assert client.alias_updates == [("task-priority-classifier", "production", "4")]
    assert "old_production_version=none" in logs[-1]


def test_rollback_to_current_version_is_an_idempotent_noop():
    production = model_version(4)
    client = StubMlflowClient(versions={4: production}, production=production)
    logs = []

    result = rollback_production(client, "4", log=logs.append)

    assert result.decision == "NOOP_ALREADY_CURRENT"
    assert result.old_production_version == "4"
    assert client.alias_updates == []
    assert client.aliases["production"] is production
    assert "decision=NOOP_ALREADY_CURRENT" in logs[-1]


def test_unknown_version_fails_without_changing_the_alias():
    production = model_version(3)
    client = StubMlflowClient(versions={3: production}, production=production)

    with pytest.raises(RollbackError, match="does not exist"):
        rollback_production(client, "99")

    assert client.alias_updates == []
    assert client.aliases["production"] is production


@pytest.mark.parametrize("version", ["0", "-1", "abc", "  "])
def test_invalid_version_input_fails_without_registry_access(version):
    production = model_version(3)
    client = StubMlflowClient(versions={3: production}, production=production)

    with pytest.raises(RollbackError, match="positive integers"):
        rollback_production(client, version)

    assert client.version_lookups == []
    assert client.alias_updates == []
    assert client.aliases["production"] is production


def test_registry_returning_a_different_version_fails_closed():
    production = model_version(3)
    client = StubMlflowClient(versions={3: production}, production=production)
    client.versions["7"] = model_version(8)

    with pytest.raises(RollbackError, match="different model version"):
        rollback_production(client, "7")

    assert client.alias_updates == []
    assert client.aliases["production"] is production


def test_missing_production_alias_accepts_mlflow_invalid_parameter_response():
    class InvalidParameterMissingAliasClient(StubMlflowClient):
        def get_model_version_by_alias(self, name, alias):
            raise MlflowInvalidParameterError(
                f"INVALID_PARAMETER_VALUE: Registered model alias {alias} not found."
            )

    client = InvalidParameterMissingAliasClient(versions={4: model_version(4)})

    result = rollback_production(client, "4")

    assert result.decision == "ROLLED_BACK"
    assert result.old_production_version is None
    assert client.alias_updates == [("task-priority-classifier", "production", "4")]


def test_unrelated_alias_lookup_errors_are_not_treated_as_missing_alias():
    class RegistryUnavailableClient(StubMlflowClient):
        def get_model_version_by_alias(self, name, alias):
            raise RuntimeError("registry unavailable")

    production = model_version(3)
    client = RegistryUnavailableClient(
        versions={3: production, 5: model_version(5)}, production=production
    )

    with pytest.raises(RuntimeError, match="registry unavailable"):
        rollback_production(client, "5")

    assert client.alias_updates == []
    assert client.aliases["production"] is production


def test_main_requires_model_version(monkeypatch, capsys):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.test:5000")
    monkeypatch.delenv("MODEL_VERSION", raising=False)

    assert main() == 1
    assert "MODEL_VERSION is required" in capsys.readouterr().err


def test_main_returns_failure_when_registry_is_unavailable(monkeypatch, capsys):
    class RegistryUnavailableClient(StubMlflowClient):
        def get_model_version(self, name, version):
            raise RuntimeError("registry unavailable")

    production = model_version(3)
    client = RegistryUnavailableClient(versions={3: production}, production=production)
    tracking = ModuleType("mlflow.tracking")
    tracking.MlflowClient = lambda tracking_uri: client
    mlflow = ModuleType("mlflow")
    mlflow.__path__ = []

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.tracking", tracking)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.test:5000")
    monkeypatch.setenv("MODEL_VERSION", "5")

    assert main() == 1
    assert "production rollback failed:" in capsys.readouterr().err
    assert client.alias_updates == []
    assert client.aliases["production"] is production
