from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_repo_server_has_memory_headroom_and_bounded_manifest_generation() -> None:
    terraform = (ROOT / "terraform" / "platform" / "main.tf").read_text(encoding="utf-8")
    repo_server = terraform.split("    repoServer = {", maxsplit=1)[1].split(
        "    applicationSet = {", maxsplit=1
    )[0]

    assert 'memory = "512Mi"' in repo_server
    assert 'memory = "1Gi"' in repo_server
    assert '"reposerver.parallelism.limit" = "1"' in terraform


def test_root_sync_waves_wait_for_monitoring_before_serving() -> None:
    monitoring = (
        ROOT / "deploy" / "argocd" / "templates" / "monitoring-application.yaml"
    ).read_text(encoding="utf-8")
    staging = (
        ROOT / "deploy" / "argocd" / "templates" / "serving-staging-application.yaml"
    ).read_text(encoding="utf-8")
    production = (
        ROOT / "deploy" / "argocd" / "templates" / "serving-production-application.yaml"
    ).read_text(encoding="utf-8")

    assert 'argocd.argoproj.io/sync-wave: "1"' in monitoring
    assert 'argocd.argoproj.io/sync-wave: "2"' in staging
    assert 'argocd.argoproj.io/sync-wave: "2"' in production


def test_application_health_requires_synced_and_healthy_child() -> None:
    terraform = (ROOT / "terraform" / "platform" / "main.tf").read_text(encoding="utf-8")

    assert '"resource.customizations.health.argoproj.io_Application"' in terraform
    assert 'obj.status.sync.status ~= "Synced"' in terraform
    assert 'obj.status.health.status or "Progressing"' in terraform
    assert 'condition.type == "ComparisonError"' in terraform
    assert 'phase == "Error" or phase == "Failed"' in terraform
