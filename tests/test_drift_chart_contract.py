from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_drift_cronjob_is_suspended_and_uses_restricted_identity():
    values = yaml.safe_load((ROOT / "deploy/charts/ml-jobs/values.yaml").read_text())
    template = (ROOT / "deploy/charts/ml-jobs/templates/drift-cronjob.yaml").read_text()

    assert "drift" in values["serviceAccounts"]
    assert values["drift"]["suspend"] is True
    assert values["drift"]["schedule"] == "0 */6 * * *"
    assert "concurrencyPolicy: Forbid" in template
    assert "serviceAccountName: drift" in template
    assert "tasks-db-credentials" in template
    assert "readOnlyRootFilesystem: true" in template
    assert "capabilities:" in template and 'drop: ["ALL"]' in template
    assert "ARTIFACTS_BUCKET" in template
    assert "PUSHGATEWAY_URL" in template


def test_ml_jobs_application_receives_existing_infra_values_and_image_pin():
    app = (ROOT / "deploy/argocd/templates/ml-jobs-application.yaml").read_text()
    image_workflow = (ROOT / ".github/workflows/image-release.yml").read_text()

    assert "values-image.yaml" in app
    assert 'regexReplaceAll "/mlflow$"' in app
    assert ".Values.mlflowArtifactsBucket" in app
    assert ".Values.mlflowRegion" in app
    assert 'deploy/charts/ml-jobs/values-image.yaml' in image_workflow


def test_drift_runtime_is_pinned_and_installed_in_ml_jobs_image():
    requirements = (ROOT / "jobs/requirements-drift.txt").read_text()
    dockerfile = (ROOT / "images/ml-jobs/Dockerfile").read_text()

    assert "evidently==0.7.23" in requirements
    assert "boto3==1.43.127" in requirements
    assert "-r /tmp/drift-requirements.txt" in dockerfile
    assert "jobs/drift.py" in dockerfile
