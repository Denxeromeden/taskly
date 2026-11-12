import sys
from types import ModuleType, SimpleNamespace

import pytest

from jobs.promote_production import (
    PromotionError,
    main,
    promote_staging_to_production,
)


class MlflowMissingAliasError(Exception):
    error_code = "RESOURCE_DOES_NOT_EXIST"


class MlflowInvalidParameterError(Exception):
    error_code = "INVALID_PARAMETER_VALUE"


class StubMlflowClient:
    def __init__(self, versions, metrics, production=None, staging=None):
        self.versions = versions
        self.metrics = metrics
        self.aliases = {}
        if production is not None:
            self.aliases["production"] = production
        if staging is not None:
            self.aliases["staging"] = staging
        self.alias_updates = []
        self.run_lookups = []
        self.alias_lookups = []

    def get_run(self, run_id):
        self.run_lookups.append(run_id)
        if run_id not in self.metrics:
            raise AssertionError(f"Unexpected run lookup: {run_id}")
        return SimpleNamespace(data=SimpleNamespace(metrics=self.metrics[run_id]))

    def get_model_version_by_alias(self, name, alias):
        self.alias_lookups.append((name, alias))
        if alias not in self.aliases:
            raise MlflowMissingAliasError(f"{alias} alias does not exist")
        return self.aliases[alias]

    def set_registered_model_alias(self, name, alias, version):
        self.alias_updates.append((name, alias, str(version)))
        self.aliases[alias] = SimpleNamespace(version=str(version), run_id=None)


def model_version(version, run_id):
    return SimpleNamespace(version=str(version), run_id=run_id)


def test_first_release_initializes_production_from_the_exact_staging_version():
    staging = model_version(3, "staging-run")
    client = StubMlflowClient(
        versions=[model_version(9, "newer-run"), staging],
        metrics={"newer-run": {"f1_macro": 0.99}, "staging-run": {"f1_macro": 0.82}},
        staging=staging,
    )
    logs = []

    result = promote_staging_to_production(client, log=logs.append)

    assert result.decision == "INITIALIZED_PRODUCTION"
    assert result.staging_version == "3"
    assert result.staging_metric == pytest.approx(0.82)
    assert result.production_version is None
    assert client.alias_updates == [("task-priority-classifier", "production", "3")]
    assert client.aliases["production"].version == "3"
    assert client.run_lookups == ["staging-run"]
    assert "production_version=none" in logs[-1]
    assert "staging_f1_macro=0.820000" in logs[-1]
    assert "decision=INITIALIZED_PRODUCTION" in logs[-1]


def test_better_staging_candidate_updates_production():
    staging = model_version(4, "staging-run")
    production = model_version(3, "production-run")
    client = StubMlflowClient(
        versions=[staging, production],
        metrics={"staging-run": {"f1_macro": 0.86}, "production-run": {"f1_macro": 0.82}},
        production=production,
        staging=staging,
    )
    logs = []

    result = promote_staging_to_production(client, log=logs.append)

    assert result.decision == "PROMOTED_TO_PRODUCTION"
    assert client.alias_updates == [("task-priority-classifier", "production", "4")]
    assert client.aliases["production"].version == "4"
    assert "production_version=3" in logs[-1]
    assert "staging_f1_macro=0.860000" in logs[-1]
    assert "production_f1_macro=0.820000" in logs[-1]


def test_equal_staging_candidate_updates_production():
    staging = model_version(4, "staging-run")
    client = StubMlflowClient(
        versions=[staging],
        metrics={"staging-run": {"f1_macro": 0.82}, "production-run": {"f1_macro": 0.82}},
        production=model_version(3, "production-run"),
        staging=staging,
    )

    result = promote_staging_to_production(client)

    assert result.decision == "PROMOTED_TO_PRODUCTION"
    assert client.alias_updates == [("task-priority-classifier", "production", "4")]


def test_lower_staging_metric_is_rejected_without_changing_any_alias():
    staging = model_version(4, "staging-run")
    production = model_version(3, "production-run")
    client = StubMlflowClient(
        versions=[staging, production],
        metrics={"staging-run": {"f1_macro": 0.81}, "production-run": {"f1_macro": 0.82}},
        production=production,
        staging=staging,
    )
    logs = []

    with pytest.raises(PromotionError, match="no model alias was changed"):
        promote_staging_to_production(client, log=logs.append)

    assert client.alias_updates == []
    assert client.aliases["production"] is production
    assert client.aliases["staging"] is staging
    assert "decision=REJECTED_METRIC_REGRESSION" in logs[-1]


def test_same_version_is_an_idempotent_successful_noop():
    current = model_version(4, "staging-run")
    client = StubMlflowClient(
        versions=[current],
        metrics={"staging-run": {"f1_macro": 0.82}},
        production=current,
        staging=current,
    )
    logs = []

    result = promote_staging_to_production(client, log=logs.append)

    assert result.decision == "NOOP_ALREADY_CURRENT"
    assert result.staging_version == "4"
    assert result.production_version == "4"
    assert client.alias_updates == []
    assert client.aliases["production"] is current
    assert client.aliases["staging"] is current
    assert "decision=NOOP_ALREADY_CURRENT" in logs[-1]


@pytest.mark.parametrize(
    "staging_metrics,production_metrics",
    [
        ({}, {"f1_macro": 0.8}),
        ({"f1_macro": 0.9}, {}),
        ({"f1_macro": "0.9"}, {"f1_macro": 0.8}),
        ({"f1_macro": True}, {"f1_macro": 0.8}),
        ({"f1_macro": float("nan")}, {"f1_macro": 0.8}),
        ({"f1_macro": 0.9}, {"f1_macro": float("inf")}),
    ],
)
def test_missing_or_non_numeric_metric_fails_closed(
    staging_metrics, production_metrics
):
    staging = model_version(4, "staging-run")
    production = model_version(3, "production-run")
    client = StubMlflowClient(
        versions=[staging, production],
        metrics={"staging-run": staging_metrics, "production-run": production_metrics},
        production=production,
        staging=staging,
    )
    logs = []

    with pytest.raises(PromotionError, match="no model alias was changed"):
        promote_staging_to_production(client, log=logs.append)

    assert client.alias_updates == []
    assert client.aliases["production"] is production
    assert client.aliases["staging"] is staging
    assert "decision=FAIL_CLOSED_MISSING_METRIC" in logs[-1]


def test_missing_staging_alias_fails_closed():
    production = model_version(3, "production-run")
    client = StubMlflowClient(
        versions=[production],
        metrics={"production-run": {"f1_macro": 0.8}},
        production=production,
    )

    with pytest.raises(PromotionError, match="No @staging model version"):
        promote_staging_to_production(client)

    assert client.alias_updates == []
    assert client.aliases["production"] is production
    assert client.alias_lookups == [("task-priority-classifier", "staging")]


def test_first_release_handles_mlflow_invalid_parameter_missing_alias():
    class InvalidParameterMissingAliasClient(StubMlflowClient):
        def get_model_version_by_alias(self, name, alias):
            self.alias_lookups.append((name, alias))
            if alias == "production":
                raise MlflowInvalidParameterError(
                    f"INVALID_PARAMETER_VALUE: Registered model alias {alias} not found."
                )
            return super().get_model_version_by_alias(name, alias)

    staging = model_version(4, "staging-run")
    client = InvalidParameterMissingAliasClient(
        versions=[staging],
        metrics={"staging-run": {"f1_macro": 0.82}},
        staging=staging,
    )

    result = promote_staging_to_production(client)

    assert result.decision == "INITIALIZED_PRODUCTION"
    assert client.alias_updates == [("task-priority-classifier", "production", "4")]


def test_other_alias_lookup_errors_are_not_treated_as_missing_alias():
    class RegistryUnavailableClient(StubMlflowClient):
        def get_model_version_by_alias(self, name, alias):
            raise RuntimeError("registry unavailable")

    staging = model_version(4, "staging-run")
    production = model_version(3, "production-run")
    client = RegistryUnavailableClient(
        versions=[staging, production],
        metrics={"staging-run": {"f1_macro": 0.9}, "production-run": {"f1_macro": 0.8}},
        production=production,
        staging=staging,
    )

    with pytest.raises(RuntimeError, match="registry unavailable"):
        promote_staging_to_production(client)

    assert client.alias_updates == []
    assert client.aliases["production"] is production
    assert client.aliases["staging"] is staging


def test_registry_read_failure_leaves_aliases_unchanged():
    class FailingRegistryClient(StubMlflowClient):
        def get_run(self, run_id):
            if run_id == "staging-run":
                raise RuntimeError("staging run unavailable")
            return super().get_run(run_id)

    staging = model_version(4, "staging-run")
    production = model_version(3, "production-run")
    client = FailingRegistryClient(
        versions=[staging, production],
        metrics={"staging-run": {"f1_macro": 0.9}, "production-run": {"f1_macro": 0.8}},
        production=production,
        staging=staging,
    )

    with pytest.raises(RuntimeError, match="staging run unavailable"):
        promote_staging_to_production(client)

    assert client.alias_updates == []
    assert client.aliases["production"] is production
    assert client.aliases["staging"] is staging


def test_main_returns_failure_when_registry_is_unavailable(monkeypatch, capsys):
    class RegistryUnavailableClient(StubMlflowClient):
        def get_model_version_by_alias(self, name, alias):
            raise RuntimeError("registry unavailable")

    staging = model_version(4, "staging-run")
    production = model_version(3, "production-run")
    client = RegistryUnavailableClient(
        versions=[staging, production],
        metrics={},
        production=production,
        staging=staging,
    )
    tracking = ModuleType("mlflow.tracking")
    tracking.MlflowClient = lambda tracking_uri: client
    mlflow = ModuleType("mlflow")
    mlflow.__path__ = []

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.tracking", tracking)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://mlflow.test:5000")

    assert main() == 1
    assert "production promotion failed: registry unavailable" in capsys.readouterr().err
    assert client.alias_updates == []
    assert client.aliases["production"] is production
    assert client.aliases["staging"] is staging


def test_main_requires_tracking_uri(monkeypatch, capsys):
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)

    assert main() == 1
    assert "MLFLOW_TRACKING_URI is required" in capsys.readouterr().err
