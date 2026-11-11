import sys
from types import ModuleType, SimpleNamespace

import pytest

from jobs.promote import PromotionError, main, promote_latest_model


class MlflowMissingAliasError(Exception):
    error_code = "RESOURCE_DOES_NOT_EXIST"


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

    def search_model_versions(self, *, filter_string):
        assert filter_string == "name = 'task-priority-classifier'"
        return list(self.versions)

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
        matching_version = next(
            (item for item in self.versions if str(item.version) == str(version)),
            SimpleNamespace(version=str(version), run_id=None),
        )
        self.aliases[alias] = matching_version


def model_version(version, run_id):
    return SimpleNamespace(version=str(version), run_id=run_id)


def test_first_candidate_sets_staging_only_and_logs_initial_decision():
    client = StubMlflowClient(
        versions=[model_version(1, "run-1"), model_version(2, "run-2")],
        metrics={"run-1": {"f1_macro": 0.71}, "run-2": {"f1_macro": 0.82}},
    )
    logs = []

    result = promote_latest_model(client, log=logs.append)

    assert result.decision == "INITIAL_STAGING_CANDIDATE"
    assert result.candidate_version == "2"
    assert result.candidate_metric == pytest.approx(0.82)
    assert result.production_version is None
    assert client.alias_updates == [("task-priority-classifier", "staging", "2")]
    assert client.aliases["staging"].version == "2"
    assert "production" not in client.aliases
    assert client.alias_lookups == [("task-priority-classifier", "production")]
    assert "registered versions found" in logs[0]
    assert "decision=INITIAL_STAGING_CANDIDATE" in logs[1]


def test_equal_candidate_metric_passes_and_updates_staging():
    production = model_version(3, "production-run")
    client = StubMlflowClient(
        versions=[model_version(4, "candidate-run")],
        metrics={"candidate-run": {"f1_macro": 0.8}, "production-run": {"f1_macro": 0.8}},
        production=production,
    )

    result = promote_latest_model(client)

    assert result.decision == "PROMOTED_TO_STAGING"
    assert result.production_version == "3"
    assert result.production_metric == pytest.approx(0.8)
    assert client.alias_updates == [("task-priority-classifier", "staging", "4")]
    assert client.aliases["production"] is production


def test_better_candidate_metric_updates_staging():
    client = StubMlflowClient(
        versions=[model_version(5, "candidate-run")],
        metrics={"candidate-run": {"f1_macro": 0.91}, "production-run": {"f1_macro": 0.9}},
        production=model_version(4, "production-run"),
    )

    result = promote_latest_model(client)

    assert result.decision == "PROMOTED_TO_STAGING"
    assert client.alias_updates == [("task-priority-classifier", "staging", "5")]


def test_regressed_candidate_is_rejected_without_changing_any_alias():
    production = model_version(5, "production-run")
    staging = model_version(4, "staging-run")
    client = StubMlflowClient(
        versions=[model_version(6, "candidate-run"), production, staging],
        metrics={
            "candidate-run": {"f1_macro": 0.79},
            "production-run": {"f1_macro": 0.8},
            "staging-run": {"f1_macro": 0.84},
        },
        production=production,
        staging=staging,
    )
    logs = []

    result = promote_latest_model(client, log=logs.append)

    assert result.decision == "REJECTED_METRIC_REGRESSION"
    assert client.alias_updates == []
    assert client.aliases["staging"] is staging
    assert client.aliases["production"] is production
    assert "production_version=5" in logs[-1]
    assert "candidate_f1_macro=0.790000" in logs[-1]
    assert "production_f1_macro=0.800000" in logs[-1]


@pytest.mark.parametrize(
    "candidate_metric,expected_decision,expected_update",
    [
        (0.7999996, "REJECTED_METRIC_REGRESSION", False),
        (0.8000004, "PROMOTED_TO_STAGING", True),
    ],
)
def test_metric_comparison_uses_full_precision(
    candidate_metric, expected_decision, expected_update
):
    client = StubMlflowClient(
        versions=[model_version(7, "candidate-run")],
        metrics={"candidate-run": {"f1_macro": candidate_metric}, "production-run": {"f1_macro": 0.8}},
        production=model_version(6, "production-run"),
    )

    result = promote_latest_model(client)

    assert f"{candidate_metric:.6f}" == "0.800000"
    assert result.decision == expected_decision
    assert bool(client.alias_updates) is expected_update


def test_candidate_is_compared_to_production_not_the_current_staging_alias():
    production = model_version(6, "production-run")
    staging = model_version(8, "staging-run")
    candidate = model_version(9, "candidate-run")
    client = StubMlflowClient(
        versions=[production, staging, candidate],
        metrics={
            "production-run": {"f1_macro": 0.80},
            "staging-run": {"f1_macro": 0.99},
            "candidate-run": {"f1_macro": 0.85},
        },
        production=production,
        staging=staging,
    )

    result = promote_latest_model(client)

    assert result.decision == "PROMOTED_TO_STAGING"
    assert result.production_version == "6"
    assert client.run_lookups == ["candidate-run", "production-run"]
    assert client.aliases["staging"].version == "9"
    assert client.aliases["production"] is production
    assert client.alias_updates == [("task-priority-classifier", "staging", "9")]


def test_rejected_candidate_preserves_previous_staging_alias():
    production = model_version(6, "production-run")
    staging = model_version(8, "staging-run")
    client = StubMlflowClient(
        versions=[production, staging, model_version(9, "candidate-run")],
        metrics={
            "production-run": {"f1_macro": 0.80},
            "staging-run": {"f1_macro": 0.86},
            "candidate-run": {"f1_macro": 0.79},
        },
        production=production,
        staging=staging,
    )

    result = promote_latest_model(client)

    assert result.decision == "REJECTED_METRIC_REGRESSION"
    assert client.alias_updates == []
    assert client.aliases["staging"] is staging
    assert client.aliases["production"] is production
    assert client.run_lookups == ["candidate-run", "production-run"]


@pytest.mark.parametrize(
    "candidate_metrics,production_metrics",
    [
        ({}, {"f1_macro": 0.8}),
        ({"f1_macro": 0.9}, {}),
        ({"f1_macro": "0.9"}, {"f1_macro": 0.8}),
        ({"f1_macro": float("nan")}, {"f1_macro": 0.8}),
    ],
)
def test_missing_or_non_numeric_metric_fails_closed(
    candidate_metrics, production_metrics
):
    client = StubMlflowClient(
        versions=[model_version(7, "candidate-run")],
        metrics={"candidate-run": candidate_metrics, "production-run": production_metrics},
        production=model_version(6, "production-run"),
    )
    logs = []

    with pytest.raises(PromotionError, match="no model alias was changed"):
        promote_latest_model(client, log=logs.append)

    assert client.alias_updates == []
    assert "decision=FAIL_CLOSED_MISSING_METRIC" in logs[-1]


def test_no_registered_versions_fails_without_alias_changes():
    production = model_version(4, "production-run")
    staging = model_version(3, "staging-run")
    client = StubMlflowClient(
        versions=[], metrics={}, production=production, staging=staging
    )

    with pytest.raises(PromotionError, match="No registered versions"):
        promote_latest_model(client)

    assert client.alias_updates == []
    assert client.aliases["production"] is production
    assert client.aliases["staging"] is staging
    assert client.alias_lookups == []


def test_other_alias_lookup_errors_are_not_treated_as_missing_alias():
    class RegistryUnavailableClient(StubMlflowClient):
        def get_model_version_by_alias(self, name, alias):
            raise RuntimeError("registry unavailable")

    production = model_version(6, "production-run")
    staging = model_version(5, "staging-run")
    client = RegistryUnavailableClient(
        versions=[model_version(8, "candidate-run")],
        metrics={"candidate-run": {"f1_macro": 0.9}},
        production=production,
        staging=staging,
    )

    with pytest.raises(RuntimeError, match="registry unavailable"):
        promote_latest_model(client)

    assert client.alias_updates == []
    assert client.aliases["production"] is production
    assert client.aliases["staging"] is staging


@pytest.mark.parametrize(
    "failure_point", ["search", "candidate_metric", "production_metric"]
)
def test_registry_read_failures_leave_aliases_unchanged(failure_point):
    class FailingRegistryClient(StubMlflowClient):
        def search_model_versions(self, *, filter_string):
            if failure_point == "search":
                raise RuntimeError("registry unavailable")
            return super().search_model_versions(filter_string=filter_string)

        def get_run(self, run_id):
            if failure_point == "candidate_metric" and run_id == "candidate-run":
                raise RuntimeError("candidate run unavailable")
            if failure_point == "production_metric" and run_id == "production-run":
                raise RuntimeError("production run unavailable")
            return super().get_run(run_id)

    production = model_version(6, "production-run")
    staging = model_version(5, "staging-run")
    client = FailingRegistryClient(
        versions=[model_version(7, "candidate-run")],
        metrics={"candidate-run": {"f1_macro": 0.9}, "production-run": {"f1_macro": 0.8}},
        production=production,
        staging=staging,
    )

    with pytest.raises(RuntimeError):
        promote_latest_model(client)

    assert client.alias_updates == []
    assert client.aliases["production"] is production
    assert client.aliases["staging"] is staging


def test_staging_alias_update_failure_fails_without_changing_aliases():
    class AliasUpdateFailureClient(StubMlflowClient):
        def set_registered_model_alias(self, name, alias, version):
            raise RuntimeError("alias update unavailable")

    production = model_version(6, "production-run")
    staging = model_version(5, "staging-run")
    client = AliasUpdateFailureClient(
        versions=[model_version(7, "candidate-run")],
        metrics={"candidate-run": {"f1_macro": 0.9}, "production-run": {"f1_macro": 0.8}},
        production=production,
        staging=staging,
    )

    with pytest.raises(RuntimeError, match="alias update unavailable"):
        promote_latest_model(client)

    assert client.alias_updates == []
    assert client.aliases["production"] is production
    assert client.aliases["staging"] is staging


def test_main_returns_failure_when_registry_is_unavailable(monkeypatch, capsys):
    production = model_version(6, "production-run")
    staging = model_version(5, "staging-run")

    class RegistryUnavailableClient(StubMlflowClient):
        def search_model_versions(self, *, filter_string):
            raise RuntimeError("registry unavailable")

    client = RegistryUnavailableClient(
        versions=[model_version(7, "candidate-run")],
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
    assert "promotion failed: registry unavailable" in capsys.readouterr().err
    assert client.alias_updates == []
    assert client.aliases["production"] is production
    assert client.aliases["staging"] is staging
