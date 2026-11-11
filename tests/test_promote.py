from types import SimpleNamespace

import pytest

from jobs.promote import PromotionError, promote_latest_model


class MlflowMissingAliasError(Exception):
    error_code = "RESOURCE_DOES_NOT_EXIST"


class StubMlflowClient:
    def __init__(self, versions, metrics, production=None):
        self.versions = versions
        self.metrics = metrics
        self.production = production
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
        assert alias == "production"
        if self.production is None:
            raise MlflowMissingAliasError("production alias does not exist")
        return self.production

    def set_registered_model_alias(self, name, alias, version):
        self.alias_updates.append((name, alias, str(version)))


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
    assert all(alias != "production" for _, alias, _ in client.alias_updates)
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
    client = StubMlflowClient(
        versions=[model_version(6, "candidate-run")],
        metrics={"candidate-run": {"f1_macro": 0.79}, "production-run": {"f1_macro": 0.8}},
        production=model_version(5, "production-run"),
    )
    logs = []

    result = promote_latest_model(client, log=logs.append)

    assert result.decision == "REJECTED_METRIC_REGRESSION"
    assert client.alias_updates == []
    assert "production_version=5" in logs[-1]
    assert "candidate_f1_macro=0.790000" in logs[-1]
    assert "production_f1_macro=0.800000" in logs[-1]


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
    client = StubMlflowClient(versions=[], metrics={})

    with pytest.raises(PromotionError, match="No registered versions"):
        promote_latest_model(client)

    assert client.alias_updates == []


def test_other_alias_lookup_errors_are_not_treated_as_missing_alias():
    class RegistryUnavailableClient(StubMlflowClient):
        def get_model_version_by_alias(self, name, alias):
            raise RuntimeError("registry unavailable")

    client = RegistryUnavailableClient(
        versions=[model_version(8, "candidate-run")],
        metrics={"candidate-run": {"f1_macro": 0.9}},
    )

    with pytest.raises(RuntimeError, match="registry unavailable"):
        promote_latest_model(client)

    assert client.alias_updates == []
