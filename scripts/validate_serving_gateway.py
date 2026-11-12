#!/usr/bin/env python3
"""Validate a rendered production serving chart against the Gateway contract.

The production Argo CD Application renders ``deploy/charts/serving`` with
``values-production.yaml`` plus Terraform-derived certificate/CIDR values.
This validator fails closed if the rendered output exposes anything other than
the single public ``POST /predict`` route, changes the contract source ranges,
or widens the serving pod ingress policy.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from pathlib import Path
from typing import Any

import yaml

PRODUCTION_NAMESPACE = "production"
GATEWAY_NAME = "serving-public"
HTTPROUTE_NAME = "serving-public"
LB_CONFIG_NAME = "serving-public-lb"
TARGET_GROUP_NAME = "serving-api-targets"
SERVICE_NAME = "serving-api"
NETWORK_POLICY_NAME = "serving-api-ingress"
PUBLIC_PATH = "/predict"
BACKEND_PORT = 8000
# docs/foundation-contract.yaml currently specifies public ALB client ingress.
CONTRACT_SOURCE_RANGES = ["0.0.0.0/0"]
ROUTE_KINDS = frozenset({"HTTPRoute", "GRPCRoute", "TCPRoute", "TLSRoute", "UDPRoute"})


class ContractError(ValueError):
    """Raised when rendered resources violate the public-route contract."""


def _fail(message: str) -> None:
    raise ContractError(message)


def _expect(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def load_rendered_documents(path: Path) -> list[dict[str, Any]]:
    """Read a multi-document YAML stream and return its object documents."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"cannot read {path}: {exc}") from None

    try:
        documents = [doc for doc in yaml.safe_load_all(text) if doc is not None]
    except yaml.YAMLError as exc:
        raise ContractError(f"{path} is not valid YAML: {exc}") from None

    _expect(bool(documents), f"{path} contains no Kubernetes resources")
    for document in documents:
        _expect(
            isinstance(document, dict),
            f"{path} contains a non-object YAML document",
        )
    return documents


def _kind(document: dict[str, Any]) -> str:
    return str(document.get("kind", ""))


def _metadata(document: dict[str, Any]) -> dict[str, Any]:
    metadata = document.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _name(document: dict[str, Any]) -> str:
    return str(_metadata(document).get("name", ""))


def _namespace(document: dict[str, Any]) -> str:
    return str(_metadata(document).get("namespace", ""))


def _resource(
    documents: list[dict[str, Any]],
    kind: str,
    name: str,
    namespace: str = PRODUCTION_NAMESPACE,
) -> dict[str, Any]:
    matches = [
        document
        for document in documents
        if _kind(document) == kind
        and _name(document) == name
        and _namespace(document) == namespace
    ]
    _expect(
        len(matches) == 1,
        f"expected exactly one {kind} {namespace}/{name}, found {len(matches)}",
    )
    return matches[0]


def _mapping(document: dict[str, Any], field: str, label: str) -> dict[str, Any]:
    value = document.get(field)
    _expect(
        isinstance(value, dict), f"{label}.{field} must be a mapping"
    )
    return value


def _list(document: dict[str, Any], field: str, label: str) -> list[Any]:
    value = document.get(field)
    _expect(isinstance(value, list), f"{label}.{field} must be a list")
    return value


def _validate_gateway(documents: list[dict[str, Any]]) -> None:
    gateways = [document for document in documents if _kind(document) == "Gateway"]
    _expect(len(gateways) == 1, f"production render must contain exactly one Gateway, found {len(gateways)}")

    gateway = _resource(documents, "Gateway", GATEWAY_NAME)
    label = f"Gateway {PRODUCTION_NAMESPACE}/{GATEWAY_NAME}"
    _expect(
        gateway.get("apiVersion") == "gateway.networking.k8s.io/v1",
        f"{label} must use gateway.networking.k8s.io/v1",
    )
    _expect(
        gateway.get("spec", {}).get("gatewayClassName") == "aws-alb",
        f"{label} must use the aws-alb GatewayClass",
    )

    spec = _mapping(gateway, "spec", label)
    infrastructure = _mapping(spec, "infrastructure", label)
    parameters_ref = _mapping(infrastructure, "parametersRef", label)
    _expect(
        parameters_ref
        == {
            "group": "gateway.k8s.aws",
            "kind": "LoadBalancerConfiguration",
            "name": LB_CONFIG_NAME,
        },
        f"{label} must attach LoadBalancerConfiguration {LB_CONFIG_NAME}",
    )

    listeners = _list(spec, "listeners", label)
    _expect(len(listeners) == 1, f"{label} must have exactly one listener")
    listener = listeners[0]
    _expect(isinstance(listener, dict), f"{label} listener must be a mapping")
    _expect(listener.get("name") == "https", f"{label} listener must be named https")
    _expect(
        listener.get("protocol") == "HTTPS",
        f"{label} listener must use HTTPS",
    )
    _expect(
        listener.get("port") == 443,
        f"{label} listener must use port 443",
    )
    _expect(
        "tls" not in listener,
        f"{label} must not reference a TLS Secret; the certificate comes from LoadBalancerConfiguration",
    )

    allowed_routes = _mapping(listener, "allowedRoutes", label)
    _expect(
        allowed_routes.get("kinds")
        == [{"group": "gateway.networking.k8s.io", "kind": "HTTPRoute"}],
        f"{label} must allow only HTTPRoute kinds",
    )
    _expect(
        allowed_routes.get("namespaces") == {"from": "Same"},
        f"{label} must allow routes only from the production namespace",
    )

def _validate_httproute(documents: list[dict[str, Any]]) -> None:
    route_kinds = [
        document for document in documents if _kind(document) in ROUTE_KINDS
    ]
    _expect(
        len(route_kinds) == 1,
        "the production render must contain exactly one public route, "
        f"found {len(route_kinds)}",
    )

    route = _resource(documents, "HTTPRoute", HTTPROUTE_NAME)
    label = f"HTTPRoute {PRODUCTION_NAMESPACE}/{HTTPROUTE_NAME}"
    _expect(
        route.get("apiVersion") == "gateway.networking.k8s.io/v1",
        f"{label} must use gateway.networking.k8s.io/v1",
    )

    spec = _mapping(route, "spec", label)
    parent_refs = _list(spec, "parentRefs", label)
    _expect(len(parent_refs) == 1, f"{label} must attach to exactly one listener")
    _expect(
        parent_refs[0]
        == {
            "group": "gateway.networking.k8s.io",
            "kind": "Gateway",
            "name": GATEWAY_NAME,
            "sectionName": "https",
        },
        f"{label} must attach to the {GATEWAY_NAME} https listener",
    )

    rules = _list(spec, "rules", label)
    _expect(len(rules) == 1, f"{label} must contain exactly one rule")
    rule = rules[0]
    _expect(isinstance(rule, dict), f"{label} rule must be a mapping")
    _expect("filters" not in rule, f"{label} must not add filters")

    matches = _list(rule, "matches", label)
    _expect(len(matches) == 1, f"{label} must contain exactly one match")
    match = matches[0]
    _expect(isinstance(match, dict), f"{label} match must be a mapping")
    _expect(
        set(match) == {"method", "path"},
        f"{label} must match only method and path; found {sorted(match)}",
    )
    _expect(match.get("method") == "POST", f"{label} must match POST only")
    _expect(
        match.get("path") == {"type": "Exact", "value": PUBLIC_PATH},
        f"{label} must match the exact path {PUBLIC_PATH}",
    )

    backend_refs = _list(rule, "backendRefs", label)
    _expect(len(backend_refs) == 1, f"{label} must have exactly one backend")
    _expect(
        backend_refs[0]
        == {
            "group": "",
            "kind": "Service",
            "name": SERVICE_NAME,
            "port": BACKEND_PORT,
            "weight": 1,
        },
        f"{label} must route only to {SERVICE_NAME}:{BACKEND_PORT}",
    )


def _validate_load_balancer(documents: list[dict[str, Any]]) -> None:
    configs = [
        document
        for document in documents
        if _kind(document) == "LoadBalancerConfiguration"
    ]
    _expect(
        len(configs) == 1,
        "production render must contain exactly one LoadBalancerConfiguration",
    )
    config = _resource(
        documents, "LoadBalancerConfiguration", LB_CONFIG_NAME
    )
    label = f"LoadBalancerConfiguration {PRODUCTION_NAMESPACE}/{LB_CONFIG_NAME}"
    _expect(
        config.get("apiVersion") == "gateway.k8s.aws/v1",
        f"{label} must use gateway.k8s.aws/v1",
    )

    spec = _mapping(config, "spec", label)
    _expect(
        spec.get("scheme") == "internet-facing",
        f"{label} must use the internet-facing ALB scheme",
    )
    _expect(
        spec.get("sourceRanges") == CONTRACT_SOURCE_RANGES,
        f"{label} sourceRanges must match the foundation contract "
        f"{CONTRACT_SOURCE_RANGES}",
    )

    listener_configurations = _list(spec, "listenerConfigurations", label)
    _expect(
        len(listener_configurations) == 1,
        f"{label} must configure exactly one listener",
    )
    listener = listener_configurations[0]
    _expect(
        isinstance(listener, dict)
        and listener.get("protocolPort") == "HTTPS:443",
        f"{label} must configure the HTTPS:443 listener",
    )
    certificate = listener.get("defaultCertificate")
    _expect(
        isinstance(certificate, str) and bool(certificate.strip()),
        f"{label} must supply a non-empty ACM certificate ARN",
    )


def _validate_target_group(documents: list[dict[str, Any]]) -> None:
    configs = [
        document
        for document in documents
        if _kind(document) == "TargetGroupConfiguration"
    ]
    _expect(
        len(configs) == 1,
        "production render must contain exactly one TargetGroupConfiguration",
    )
    config = _resource(
        documents, "TargetGroupConfiguration", TARGET_GROUP_NAME
    )
    label = f"TargetGroupConfiguration {PRODUCTION_NAMESPACE}/{TARGET_GROUP_NAME}"
    _expect(
        config.get("apiVersion") == "gateway.k8s.aws/v1",
        f"{label} must use gateway.k8s.aws/v1",
    )

    spec = _mapping(config, "spec", label)
    _expect(
        spec.get("targetReference")
        == {"group": "", "kind": "Service", "name": SERVICE_NAME},
        f"{label} must target Service {SERVICE_NAME}",
    )

    default_configuration = _mapping(spec, "defaultConfiguration", label)
    _expect(
        default_configuration.get("targetType") == "ip",
        f"{label} must use IP targets",
    )
    health_check = _mapping(default_configuration, "healthCheckConfig", label)
    _expect(
        health_check.get("healthCheckProtocol") == "HTTP",
        f"{label} health check must use HTTP",
    )
    _expect(
        health_check.get("healthCheckPath") == "/health",
        f"{label} health check must use GET /health",
    )
    _expect(
        health_check.get("healthCheckPort") == str(BACKEND_PORT),
        f"{label} health check must use port {BACKEND_PORT}",
    )
    _expect(
        health_check.get("matcher") == {"httpCode": "200"},
        f"{label} health check matcher must be HTTP 200 only",
    )


def _validate_network_policy(documents: list[dict[str, Any]]) -> None:
    policy = _resource(documents, "NetworkPolicy", NETWORK_POLICY_NAME)
    label = f"NetworkPolicy {PRODUCTION_NAMESPACE}/{NETWORK_POLICY_NAME}"
    spec = _mapping(policy, "spec", label)
    _expect(
        spec.get("policyTypes") == ["Ingress"],
        f"{label} must manage ingress only",
    )

    pod_selector = _mapping(spec, "podSelector", label)
    pod_labels = _mapping(pod_selector, "matchLabels", f"{label}.podSelector")
    _expect(
        pod_labels.get("app.kubernetes.io/name") == SERVICE_NAME,
        f"{label} must select only the serving-api pods",
    )
    _expect(
        isinstance(pod_labels.get("app.kubernetes.io/instance"), str)
        and bool(pod_labels["app.kubernetes.io/instance"].strip()),
        f"{label} must scope the policy to one serving-api Helm release",
    )

    ingress_rules = _list(spec, "ingress", label)
    _expect(bool(ingress_rules), f"{label} must contain at least one ingress rule")

    ip_block_cidrs: list[str] = []
    observed_ports: set[int] = set()
    for index, rule in enumerate(ingress_rules):
        _expect(isinstance(rule, dict), f"{label} ingress rule must be a mapping")
        peers = _list(rule, "from", f"{label}.ingress[{index}]")
        _expect(
            bool(peers),
            f"{label} ingress rule {index} must not allow traffic from all sources",
        )
        for source in peers:
            _expect(
                isinstance(source, dict) and len(source) == 1,
                f"{label} ingress source must contain exactly one restricted peer selector",
            )
            if "ipBlock" in source:
                ip_block = source["ipBlock"]
                _expect(
                    isinstance(ip_block, dict)
                    and set(ip_block) == {"cidr"}
                    and isinstance(ip_block.get("cidr"), str),
                    f"{label} ipBlock must contain only a CIDR string",
                )
                ip_block_cidrs.append(ip_block["cidr"])
            elif "namespaceSelector" in source:
                selector = source["namespaceSelector"]
                _expect(
                    selector
                    == {
                        "matchLabels": {
                            "kubernetes.io/metadata.name": "monitoring"
                        }
                    },
                    f"{label} may allow only the monitoring namespace as a secondary peer",
                )
            else:
                _fail(
                    f"{label} ingress source must be an ALB subnet ipBlock "
                    "or the monitoring namespace"
                )

        ports = _list(rule, "ports", f"{label}.ingress[{index}]")
        _expect(
            len(ports) == 1
            and isinstance(ports[0], dict)
            and ports[0].get("protocol") == "TCP"
            and ports[0].get("port") == BACKEND_PORT,
            f"{label} ingress must allow only TCP/{BACKEND_PORT}",
        )
        observed_ports.add(BACKEND_PORT)

    _expect(
        bool(ip_block_cidrs),
        f"{label} must allow the ALB public-subnet CIDRs from Terraform",
    )
    for cidr in ip_block_cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            _fail(f"{label} contains an invalid CIDR: {cidr!r}")
        _expect(
            network.version == 4,
            f"{label} must use IPv4 public-subnet CIDRs; found {cidr!r}",
        )
        _expect(
            network.prefixlen > 0,
            f"{label} must not allow an all-address CIDR: {cidr!r}",
        )

    _expect(
        observed_ports == {BACKEND_PORT},
        f"{label} must expose only TCP/{BACKEND_PORT}",
    )


def _validate_service(documents: list[dict[str, Any]]) -> None:
    service = _resource(documents, "Service", SERVICE_NAME)
    label = f"Service {PRODUCTION_NAMESPACE}/{SERVICE_NAME}"
    spec = _mapping(service, "spec", label)
    _expect(
        spec.get("type") == "ClusterIP",
        f"{label} must remain an internal ClusterIP Service",
    )
    ports = _list(spec, "ports", label)
    _expect(len(ports) == 1, f"{label} must expose exactly one port")
    _expect(
        ports[0].get("port") == BACKEND_PORT
        and ports[0].get("targetPort") == "http",
        f"{label} must expose {BACKEND_PORT} to the http container port",
    )

    public_services = [
        document
        for document in documents
        if _kind(document) == "Service"
        and document.get("spec", {}).get("type", "ClusterIP")
        in {"LoadBalancer", "NodePort"}
    ]
    _expect(
        not public_services,
        "the production render must not contain NodePort or LoadBalancer Services",
    )


def _validate_no_other_public_resources(documents: list[dict[str, Any]]) -> None:
    ingress_resources = [
        document for document in documents if _kind(document) == "Ingress"
    ]
    _expect(
        not ingress_resources,
        "the production render must not contain Ingress resources",
    )

    tls_secrets = [
        document
        for document in documents
        if _kind(document) == "Secret"
        and document.get("type") == "kubernetes.io/tls"
    ]
    _expect(
        not tls_secrets,
        "the production render must not contain a TLS Secret or private key",
    )


def validate(documents: list[dict[str, Any]]) -> None:
    """Validate all production Gateway-contract invariants."""
    _validate_gateway(documents)
    _validate_httproute(documents)
    _validate_load_balancer(documents)
    _validate_target_group(documents)
    _validate_network_policy(documents)
    _validate_service(documents)
    _validate_no_other_public_resources(documents)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a rendered production serving chart against the "
            " public-route contract."
        )
    )
    parser.add_argument(
        "rendered",
        type=Path,
        help="Path to the rendered multi-document YAML produced by helm template.",
    )
    args = parser.parse_args(argv)

    try:
        documents = load_rendered_documents(args.rendered)
        validate(documents)
    except ContractError as exc:
        print(f"serving gateway contract violation: {exc}", file=sys.stderr)
        return 1

    print(f"serving gateway contract: OK ({len(documents)} resources)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
