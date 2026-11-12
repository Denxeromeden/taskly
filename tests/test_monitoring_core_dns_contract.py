from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_monitoring_chart_keeps_resources_out_of_kube_system() -> None:
    values = yaml.safe_load(
        (ROOT / "deploy" / "charts" / "monitoring" / "values.yaml").read_text(encoding="utf-8")
    )
    stack = values["kube-prometheus-stack"]

    assert stack["coreDns"]["enabled"] is False
    assert stack["kubelet"]["namespace"] == "monitoring"
    assert stack["prometheusOperator"]["kubeletService"]["namespace"] == "monitoring"
    for component in ("kubeControllerManager", "kubeEtcd", "kubeScheduler", "kubeProxy"):
        assert stack[component]["enabled"] is False
