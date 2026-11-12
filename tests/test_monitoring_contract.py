from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MONITORING = ROOT / "deploy" / "charts" / "monitoring"


def read_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_monitoring_chart_dependencies_are_exact_and_locked() -> None:
    chart = read_yaml(MONITORING / "Chart.yaml")
    lock = read_yaml(MONITORING / "Chart.lock")

    assert chart["name"] == "monitoring"
    assert [dependency["name"] for dependency in chart["dependencies"]] == [
        "kube-prometheus-stack",
        "loki",
        "alloy",
        "prometheus-pushgateway",
    ]
    assert chart["dependencies"] == lock["dependencies"]
    assert all(dependency["version"] for dependency in chart["dependencies"])
    assert all(not any(token in dependency["version"] for token in "*><~") for dependency in chart["dependencies"])

    serialized = json.dumps(
        [chart["dependencies"], lock["dependencies"]],
        separators=(",", ":"),
    ).encode("utf-8")
    assert lock["digest"] == "sha256:" + hashlib.sha256(serialized).hexdigest()


def test_prometheus_and_loki_use_bounded_persistent_internal_storage() -> None:
    values = read_yaml(MONITORING / "values.yaml")
    stack = values["kube-prometheus-stack"]
    prometheus = stack["prometheus"]["prometheusSpec"]

    assert prometheus["replicas"] == 1
    assert prometheus["retention"] == "7d"
    assert prometheus["scrapeInterval"] == "30s"
    assert prometheus["storageSpec"]["volumeClaimTemplate"]["spec"]["storageClassName"] == "gp3"
    assert prometheus["storageSpec"]["volumeClaimTemplate"]["spec"]["resources"]["requests"]["storage"] == "10Gi"
    assert stack["grafana"]["service"]["type"] == "ClusterIP"
    assert stack["grafana"]["ingress"]["enabled"] is False
    assert stack["prometheus"]["service"]["type"] == "ClusterIP"
    assert stack["prometheus"]["ingress"]["enabled"] is False
    assert stack["alertmanager"]["service"]["type"] == "ClusterIP"
    assert stack["alertmanager"]["ingress"]["enabled"] is False
    assert "adminPassword" not in stack["grafana"]
    assert stack["grafana"]["admin"]["existingSecret"] == "monitoring-grafana-credentials"
    assert stack["grafana"]["rbac"]["create"] is False
    assert stack["alertmanager"]["service"]["type"] == "ClusterIP"

    loki = values["loki"]
    assert loki["deploymentMode"] == "Monolithic"
    assert loki["loki"]["storage"]["type"] == "filesystem"
    assert loki["loki"]["limits_config"]["retention_period"] == "168h"
    assert loki["singleBinary"]["persistence"]["storageClass"] == "gp3"
    assert loki["singleBinary"]["persistence"]["size"] == "10Gi"
    assert loki["singleBinary"]["service"]["type"] == "ClusterIP"
    assert loki["gateway"]["enabled"] is False


def test_alloy_uses_namespaced_pod_log_permissions_without_secret_or_host_access() -> None:
    values = read_yaml(MONITORING / "values.yaml")
    alloy = values["alloy"]

    assert alloy["controller"]["type"] == "deployment"
    assert {"name": "alloy-data", "emptyDir": {}} in alloy["controller"]["volumes"]["extra"]
    assert {"name": "alloy-tmp", "emptyDir": {}} in alloy["controller"]["volumes"]["extra"]
    assert alloy["alloy"]["storagePath"] == "/var/lib/alloy/data"
    assert {"name": "alloy-data", "mountPath": "/var/lib/alloy/data"} in alloy["alloy"]["mounts"]["extra"]
    assert {"name": "alloy-tmp", "mountPath": "/tmp"} in alloy["alloy"]["mounts"]["extra"]
    assert alloy["global"]["podSecurityContext"]["fsGroup"] == 473
    assert alloy["alloy"]["securityContext"]["runAsUser"] == 473
    assert alloy["alloy"]["securityContext"]["runAsGroup"] == 473
    assert alloy["alloy"]["securityContext"]["readOnlyRootFilesystem"] is True
    assert alloy["alloy"]["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"
    assert alloy["rbac"]["namespaces"] == ["mlflow", "ml-jobs", "staging", "production", "monitoring"]
    assert alloy["rbac"]["rules"] == [
        {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]},
        {"apiGroups": [""], "resources": ["pods/log"], "verbs": ["get"]},
    ]
    config = alloy["alloy"]["configMap"]["content"]
    assert "loki.source.kubernetes" in config
    assert 'values = ["namespace", "app", "container", "level"]' in config
    assert "secrets" not in str(alloy["rbac"]).lower()
    assert "/var/log" not in config
    assert "hostPath" not in str(alloy)
    assert "privileged: true" not in str(alloy)
    assert alloy["service"]["type"] == "ClusterIP"
    assert alloy["ingress"]["enabled"] is False


def test_pushgateway_and_dashboard_are_internal_and_complete() -> None:
    values = read_yaml(MONITORING / "values.yaml")
    pushgateway = values["prometheus-pushgateway"]
    assert pushgateway["service"]["type"] == "ClusterIP"
    assert pushgateway["ingress"]["enabled"] is False
    assert pushgateway["serviceMonitor"]["enabled"] is True
    assert pushgateway["serviceMonitor"]["telemetryPath"] == "/metrics"

    dashboard = json.loads((MONITORING / "dashboards" / "mlflowops-overview.json").read_text())
    assert dashboard["title"] == "MLFlowOps Overview"
    panel_titles = {panel["title"] for panel in dashboard["panels"]}
    assert {
        "Prediction requests / second",
        "Prediction rate by outcome",
        "Prediction error rate",
        "p95 prediction latency",
        "Loaded model version",
        "Serving desired vs available replicas",
        "Serving restarts and OOM kills",
        "Drift detected",
        "Latest drift score",
        "Age of last successful drift run",
        "Pushgateway scrape health",
        "Workload logs (serving, MLflow and Jobs)",
    } <= panel_titles


def test_required_alerts_exist_and_prometheus_rule_is_selectable() -> None:
    rules = read_yaml(MONITORING / "templates" / "prometheusrules.yaml")
    assert rules["apiVersion"] == "monitoring.coreos.com/v1"
    assert rules["metadata"]["labels"]["release"] == "monitoring"
    alerts = {
        rule["alert"]
        for group in rules["spec"]["groups"]
        for rule in group["rules"]
    }
    assert alerts == {
        "ServingHighPredictionErrorRate",
        "ServingDeploymentUnavailable",
        "ServingPodCrashLooping",
        "ServingContainerOOMKilled",
        "DriftDetected",
        "DriftJobStale",
        "DriftPushgatewayUnavailable",
    }
    assert "{{" not in (MONITORING / "templates" / "prometheusrules.yaml").read_text()


def test_serving_monitoring_is_enabled_in_both_environment_values() -> None:
    chart = ROOT / "deploy" / "charts" / "serving"
    staging = read_yaml(chart / "values-staging.yaml")
    production = read_yaml(chart / "values-production.yaml")

    for values in (staging, production):
        assert values["serviceMonitor"]["enabled"] is True
        assert values["serviceMonitor"]["interval"] == "30s"
        assert values["networkPolicy"]["allowMonitoring"] is True

    assert staging["networkPolicy"]["allowStagingSmoke"] is True
    assert staging["gateway"]["enabled"] is False
    assert production["gateway"]["enabled"] is True
    assert production["networkPolicy"]["publicSubnetCidrs"] == []

    service_monitor = (chart / "templates" / "servicemonitor.yaml").read_text()
    assert "path: /metrics" in service_monitor
    assert ".Values.serviceMonitor.interval" in service_monitor
    assert 'default "30s"' in service_monitor
    network_policy_path = chart / "templates" / "networkpolicy.yaml"
    if network_policy_path.exists():
        network_policy = network_policy_path.read_text()
        assert "kubernetes.io/metadata.name: monitoring" in network_policy
        assert "app.kubernetes.io/name: staging-smoke" in network_policy


def test_argocd_monitoring_app_and_project_scopes_are_explicit() -> None:
    app = (ROOT / "deploy" / "argocd" / "templates" / "monitoring-application.yaml").read_text()
    root_values = read_yaml(ROOT / "deploy" / "argocd" / "values.yaml")
    project = (ROOT / "deploy" / "charts" / "argocd-bootstrap" / "templates" / "appproject.yaml").read_text()

    assert "name: monitoring" in app
    assert root_values["applications"]["monitoring"]["enabled"] is True
    assert "project: platform" in app
    assert "namespace: monitoring" in app
    assert "path: deploy/charts/monitoring" in app
    assert "namespace: monitoring" in project
    assert "kind: CustomResourceDefinition" in project
    assert "kind: ClusterRole" in project
    assert "kind: ClusterRoleBinding" in project
    assert "kind: ValidatingWebhookConfiguration" in project
    assert "kind: MutatingWebhookConfiguration" in project
    assert "kind: ServiceMonitor" in project
    dashboard_rbac = (MONITORING / "templates" / "grafana-dashboard-rbac.yaml").read_text()
    assert "kind: Role" in dashboard_rbac
    assert "namespace: monitoring" in dashboard_rbac
    assert 'resources: ["configmaps"]' in dashboard_rbac
    assert 'resources: ["secrets"]' not in dashboard_rbac
    assert "kind: RoleBinding" in dashboard_rbac
    assert "name: monitoring-grafana" in dashboard_rbac
    production_app = (ROOT / "deploy" / "argocd" / "templates" / "serving-production-application.yaml").read_text()
    assert 'argocd.argoproj.io/sync-wave: "2"' in production_app


def test_workload_contract_and_runbook_describe_monitoring_interfaces() -> None:
    contract = read_yaml(ROOT / "docs" / "workload-contract.yaml")
    assert contract["monitoring"]["namespace"] == "monitoring"
    assert contract["monitoring"]["components"]["prometheus"]["retention"] == "7d"
    assert contract["serving"]["metrics"] == "GET /metrics"
    assert contract["serving"]["metrics_scrape_interval"] == "30s"

    runbook = (ROOT / "docs" / "runbooks" / "monitoring.md").read_text()
    assert "port-forward" in runbook
    secret_template = (MONITORING / "templates" / "grafana-externalsecret.yaml").read_text()
    assert "mlflowops/monitoring/grafana-admin" in secret_template
    assert "Pushgateway" in runbook
    assert "pods/log" in runbook


def test_grafana_credentials_are_generated_and_synced_without_git_secrets() -> None:
    terraform = (ROOT / "terraform" / "platform" / "monitoring-secrets.tf").read_text()
    versions = (ROOT / "terraform" / "platform" / "versions.tf").read_text()
    lock = (ROOT / "terraform" / "platform" / ".terraform.lock.hcl").read_text()
    secret = (MONITORING / "templates" / "grafana-externalsecret.yaml").read_text()

    assert 'resource "random_password" "grafana_admin"' in terraform
    assert 'length           = 40' in terraform
    assert 'name        = "mlflowops/monitoring/grafana-admin"' in terraform
    assert 'random_password.grafana_admin.result' in terraform
    assert 'random     = { source = "hashicorp/random", version = "= 3.9.1" }' in versions
    assert 'provider "registry.terraform.io/hashicorp/random"' in lock
    assert 'secretStoreRef:' in secret
    assert 'name: aws-secretsmanager' in secret
    assert 'key: mlflowops/monitoring/grafana-admin' in secret
    assert 'adminPassword:' not in (MONITORING / "values.yaml").read_text()


def test_monitoring_security_boundaries_are_documented() -> None:
    threat_model = (ROOT / "docs" / "threat-model.md").read_text()
    trust_boundaries = (ROOT / "docs" / "trust-boundaries.mmd").read_text()
    contract = read_yaml(ROOT / "docs" / "workload-contract.yaml")

    assert "Monitoring credential disclosure" in threat_model
    assert "Log collector compromise or over-collection" in threat_model
    assert "Monitoring UI exposure" in threat_model
    assert "namespace-scoped" in threat_model
    assert "Alloy's service account" in threat_model
    assert "monitoring-grafana-credentials" in trust_boundaries
    assert "Namespaced Pod and pods/log reads" in trust_boundaries
    assert "Local port-forward only" in trust_boundaries
    assert contract["monitoring"]["grafana_access"] == "kubectl_port_forward_only"
    assert contract["monitoring"]["log_collection"]["rbac"]["secret_access"] is False
    assert len(contract["monitoring"]["alerts"]) == 7
