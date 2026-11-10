#!/usr/bin/env bash
# Install pinned Gateway API CRDs required by AWS Load Balancer Controller's ALB Gateway support.
set -euo pipefail

: "${AWS_REGION:?AWS_REGION is required}"
: "${CLUSTER_NAME:?CLUSTER_NAME is required}"
: "${GATEWAY_API_VERSION:?GATEWAY_API_VERSION is required}"
: "${AWS_LBC_VERSION:?AWS_LBC_VERSION is required}"

for binary in aws kubectl; do
  command -v "$binary" >/dev/null 2>&1 || {
    echo "$binary is required to install Gateway API CRDs" >&2
    exit 2
  }
done

aws eks update-kubeconfig --region "$AWS_REGION" --name "$CLUSTER_NAME"

standard_crds_url="https://github.com/kubernetes-sigs/gateway-api/releases/download/${GATEWAY_API_VERSION}/standard-install.yaml"
aws_extension_crds_url="https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v${AWS_LBC_VERSION}/config/crd/gateway/gateway-crds.yaml"

kubectl apply --server-side=true -f "$standard_crds_url"
kubectl apply -f "$aws_extension_crds_url"

required_crds=(
  gatewayclasses.gateway.networking.k8s.io
  gateways.gateway.networking.k8s.io
  httproutes.gateway.networking.k8s.io
  grpcroutes.gateway.networking.k8s.io
  loadbalancerconfigurations.gateway.k8s.aws
  targetgroupconfigurations.gateway.k8s.aws
  listenerruleconfigurations.gateway.k8s.aws
)

for crd in "${required_crds[@]}"; do
  kubectl wait --for=condition=Established "crd/${crd}" --timeout=120s
done
