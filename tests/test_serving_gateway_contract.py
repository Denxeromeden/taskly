"""Static checks for the production public-route contract."""

from __future__ import annotations

import copy
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.validate_serving_gateway import (
    CONTRACT_SOURCE_RANGES,
    ContractError,
    load_rendered_documents,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "deploy/charts/serving"
HELM = shutil.which("helm")
HELM_SKIP = pytest.mark.skipif(HELM is None, reason="helm is not installed")
IMAGE = "ci-registry-placeholder/mlflowops/serving"
IMAGE_TAG = "a" * 40
CERTIFICATE = "arn:aws:acm:eu-central-1:123456789012:certificate/placeholder"


def valid_documents() -> list[dict]:
    """Return the minimal valid production render for validator unit tests."""
    return [
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "Gateway",
            "metadata": {"name": "serving-public", "namespace": "production"},
            "spec": {
                "gatewayClassName": "aws-alb",
                "infrastructure": {
                    "parametersRef": {
                        "group": "gateway.k8s.aws",
                        "kind": "LoadBalancerConfiguration",
                        "name": "serving-public-lb",
                    }
                },
                "listeners": [
                    {
                        "name": "https",
                        "protocol": "HTTPS",
                        "port": 443,
                        "allowedRoutes": {
                            "kinds": [
                                {
                                    "group": "gateway.networking.k8s.io",
                                    "kind": "HTTPRoute",
                                }
                            ],
                            "namespaces": {"from": "Same"},
                        },
                    }
                ],
            },
        },
        {
            "apiVersion": "gateway.networking.k8s.io/v1",
            "kind": "HTTPRoute",
            "metadata": {"name": "serving-public", "namespace": "production"},
            "spec": {
                "parentRefs": [
                    {
                        "group": "gateway.networking.k8s.io",
                        "kind": "Gateway",
                        "name": "serving-public",
                        "sectionName": "https",
                    }
                ],
                "rules": [
                    {
                        "matches": [
                            {
                                "method": "POST",
                                "path": {"type": "Exact", "value": "/predict"},
                            }
                        ],
                        "backendRefs": [
                            {
                                "group": "",
                                "kind": "Service",
                                "name": "serving-api",
                                "port": 8000,
                                "weight": 1,
                            }
                        ],
                    }
                ],
            },
        },
        {
            "apiVersion": "gateway.k8s.aws/v1",
            "kind": "LoadBalancerConfiguration",
            "metadata": {
                "name": "serving-public-lb",
                "namespace": "production",
            },
            "spec": {
                "scheme": "internet-facing",
                "sourceRanges": ["0.0.0.0/0"],
                "listenerConfigurations": [
                    {
                        "protocolPort": "HTTPS:443",
                        "defaultCertificate": CERTIFICATE,
                    }
                ],
            },
        },
        {
            "apiVersion": "gateway.k8s.aws/v1",
            "kind": "TargetGroupConfiguration",
            "metadata": {
                "name": "serving-api-targets",
                "namespace": "production",
            },
            "spec": {
                "targetReference": {
                    "group": "",
                    "kind": "Service",
                    "name": "serving-api",
                },
                "defaultConfiguration": {
                    "targetType": "ip",
                    "healthCheckConfig": {
                        "healthCheckProtocol": "HTTP",
                        "healthCheckPath": "/health",
                        "healthCheckPort": "8000",
                        "matcher": {"httpCode": "200"},
                    },
                },
            },
        },
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "serving-api-ingress",
                "namespace": "production",
            },
            "spec": {
                "podSelector": {
                    "matchLabels": {
                        "app.kubernetes.io/name": "serving-api",
                        "app.kubernetes.io/instance": "serving-api",
                    }
                },
                "policyTypes": ["Ingress"],
                "ingress": [
                    {
                        "from": [
                            {"ipBlock": {"cidr": "10.0.0.0/24"}},
                            {"ipBlock": {"cidr": "10.0.1.0/24"}},
                        ],
                        "ports": [{"protocol": "TCP", "port": 8000}],
                    }
                ],
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "serving-api", "namespace": "production"},
            "spec": {
                "type": "ClusterIP",
                "ports": [{"name": "http", "port": 8000, "targetPort": "http"}],
            },
        },
    ]


def test_valid_contract_passes():
    validate(valid_documents())


def test_foundation_contract_matches_validator_source_ranges():
    contract = yaml.safe_load(
        (ROOT / "docs/foundation-contract.yaml").read_text(encoding="utf-8")
    )
    source_ranges = contract["gateway_api"]["serving"][
        "load_balancer_configuration"
    ]["source_ranges"]
    assert source_ranges == CONTRACT_SOURCE_RANGES


def test_catch_all_route_is_rejected():
    documents = valid_documents()
    route = documents[1]
    route["spec"]["rules"][0]["matches"][0]["path"] = {
        "type": "PathPrefix",
        "value": "/",
    }

    with pytest.raises(ContractError, match="exact path"):
        validate(documents)


def test_extra_public_route_is_rejected():
    documents = valid_documents()
    extra = copy.deepcopy(documents[1])
    extra["metadata"]["name"] = "serving-health"
    extra["spec"]["rules"][0]["matches"][0] = {
        "method": "GET",
        "path": {"type": "Exact", "value": "/health"},
    }
    documents.append(extra)

    with pytest.raises(ContractError, match="exactly one public route"):
        validate(documents)


def test_route_must_not_add_header_or_query_matches():
    documents = valid_documents()
    documents[1]["spec"]["rules"][0]["matches"][0]["headers"] = [
        {"name": "X-Test", "value": "1"}
    ]

    with pytest.raises(ContractError, match="only method and path"):
        validate(documents)


def test_gateway_must_not_allow_routes_from_all_namespaces():
    documents = valid_documents()
    documents[0]["spec"]["listeners"][0]["allowedRoutes"]["namespaces"] = {
        "from": "All"
    }

    with pytest.raises(ContractError, match="production namespace"):
        validate(documents)


def test_gateway_must_not_reference_a_tls_secret():
    documents = valid_documents()
    documents[0]["spec"]["listeners"][0]["tls"] = {
        "mode": "Terminate",
        "certificateRefs": [{"name": "serving-tls"}],
    }

    with pytest.raises(ContractError, match="TLS Secret"):
        validate(documents)


def test_backend_must_be_serving_api_on_port_8000():
    documents = valid_documents()
    documents[1]["spec"]["rules"][0]["backendRefs"][0]["port"] = 8080

    with pytest.raises(ContractError, match="serving-api:8000"):
        validate(documents)


def test_load_balancer_source_ranges_must_match_contract():
    documents = valid_documents()
    documents[2]["spec"]["sourceRanges"] = ["10.0.0.0/8"]

    with pytest.raises(ContractError, match="foundation contract"):
        validate(documents)


def test_target_group_health_check_must_use_internal_health():
    documents = valid_documents()
    documents[3]["spec"]["defaultConfiguration"]["healthCheckConfig"][
        "healthCheckPath"
    ] = "/ready"

    with pytest.raises(ContractError, match="GET /health"):
        validate(documents)


def test_target_group_health_check_matcher_must_be_200_only():
    documents = valid_documents()
    documents[3]["spec"]["defaultConfiguration"]["healthCheckConfig"]["matcher"] = {
        "httpCode": "200-299"
    }

    with pytest.raises(ContractError, match="HTTP 200"):
        validate(documents)


def test_network_policy_rejects_all_address_cidr():
    documents = valid_documents()
    documents[4]["spec"]["ingress"][0]["from"][0]["ipBlock"]["cidr"] = "0.0.0.0/0"

    with pytest.raises(ContractError, match="all-address CIDR"):
        validate(documents)


def test_network_policy_rejects_invalid_cidr():
    documents = valid_documents()
    documents[4]["spec"]["ingress"][0]["from"][0]["ipBlock"]["cidr"] = "not-a-cidr"

    with pytest.raises(ContractError, match="invalid CIDR"):
        validate(documents)


def test_network_policy_rejects_an_empty_from_rule():
    documents = valid_documents()
    documents[4]["spec"]["ingress"].append(
        {"from": [], "ports": [{"protocol": "TCP", "port": 8000}]}
    )

    with pytest.raises(ContractError, match="all sources"):
        validate(documents)


def test_network_policy_rejects_an_unrestricted_namespace_selector():
    documents = valid_documents()
    documents[4]["spec"]["ingress"].append(
        {
            "from": [{"namespaceSelector": {}}],
            "ports": [{"protocol": "TCP", "port": 8000}],
        }
    )

    with pytest.raises(ContractError, match="monitoring namespace"):
        validate(documents)


def test_network_policy_allows_only_the_monitoring_namespace_when_enabled():
    documents = valid_documents()
    documents[4]["spec"]["ingress"].append(
        {
            "from": [
                {
                    "namespaceSelector": {
                        "matchLabels": {"kubernetes.io/metadata.name": "monitoring"}
                    }
                }
            ],
            "ports": [{"protocol": "TCP", "port": 8000}],
        }
    )

    validate(documents)


def test_additional_gateway_resource_is_rejected():
    documents = valid_documents()
    extra = copy.deepcopy(documents[0])
    extra["metadata"]["name"] = "unexpected-public-gateway"
    documents.append(extra)

    with pytest.raises(ContractError, match="exactly one Gateway"):
        validate(documents)


def test_load_balancer_service_is_rejected():
    documents = valid_documents()
    documents.append(
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "unexpected-public-service", "namespace": "production"},
            "spec": {"type": "LoadBalancer"},
        }
    )

    with pytest.raises(ContractError, match="NodePort or LoadBalancer Services"):
        validate(documents)


def test_argocd_passes_public_subnet_cidrs_as_indexed_list_parameters():
    bootstrap_template = (
        ROOT
        / "deploy/charts/argocd-bootstrap/templates/root-application.yaml"
    ).read_text(encoding="utf-8")
    production_template = (
        ROOT / "deploy/argocd/templates/serving-production-application.yaml"
    ).read_text(encoding="utf-8")

    assert "publicSubnetCidrs[{{ $index }}]" in bootstrap_template
    assert "networkPolicy.publicSubnetCidrs[{{ $index }}]" in production_template
    assert 'join "," .Values.publicSubnetCidrs' not in bootstrap_template
    assert 'join "," .Values.publicSubnetCidrs' not in production_template
    assert "publicSubnetCidrs must be populated from the infra Terraform" in production_template


def test_tls_secret_is_rejected():
    documents = valid_documents()
    documents.append(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "serving-tls", "namespace": "production"},
            "type": "kubernetes.io/tls",
            "data": {"tls.key": "cHJpdmF0ZS1rZXk="},
        }
    )

    with pytest.raises(ContractError, match="must not contain a TLS Secret"):
        validate(documents)


def run_helm_template(cidrs: str = '["10.0.0.0/24","10.0.1.0/24"]') -> subprocess.CompletedProcess:
    assert HELM is not None
    return subprocess.run(
        [
            HELM,
            "template",
            "serving-production",
            str(CHART),
            "--namespace",
            "production",
            "--values",
            str(CHART / "values-production.yaml"),
            "--set-string",
            f"image.repository={IMAGE}",
            "--set-string",
            f"image.tag={IMAGE_TAG}",
            "--set-string",
            f"gateway.certificateArn={CERTIFICATE}",
            "--set-json",
            f"networkPolicy.publicSubnetCidrs={cidrs}",
        ],
        text=True,
        capture_output=True,
        check=False,
    )


@HELM_SKIP
def test_rendered_production_chart_satisfies_contract(tmp_path):
    result = run_helm_template()
    assert result.returncode == 0, result.stderr

    rendered = tmp_path / "serving-production.yaml"
    rendered.write_text(result.stdout, encoding="utf-8")
    validate(load_rendered_documents(rendered))


@HELM_SKIP
@pytest.mark.parametrize(
    "cidrs",
    [
        "[]",
        '["0.0.0.0/0"]',
        '["::/0"]',
        '["10.0.0.0/24","not-a-cidr"]',
        '["10.0.0.0/24","0.0.0.0/0"]',
    ],
)
def test_rendered_production_chart_rejects_unsafe_subnet_cidrs(cidrs):
    result = run_helm_template(cidrs)

    assert result.returncode != 0
    assert "networkPolicy.publicSubnetCidrs" in result.stderr
