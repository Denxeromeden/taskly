#!/usr/bin/env bash
# Remove Kubernetes resources that create AWS dependencies. This script never destroys Terraform stacks.
set -euo pipefail
CLUSTER="${CLUSTER_NAME:-mlflowops-demo}"
echo "Removing Gateway API routes, Gateways, and LoadBalancer Services from cluster ${CLUSTER}..."
kubectl delete httproutes.gateway.networking.k8s.io --all --all-namespaces --wait=true || true
kubectl delete grpcroutes.gateway.networking.k8s.io --all --all-namespaces --wait=true || true
kubectl delete gateways.gateway.networking.k8s.io --all --all-namespaces --wait=true || true
kubectl delete listenerruleconfigurations.gateway.k8s.aws --all --all-namespaces --wait=true || true
kubectl delete targetgroupconfigurations.gateway.k8s.aws --all --all-namespaces --wait=true || true
kubectl delete loadbalancerconfigurations.gateway.k8s.aws --all --all-namespaces --wait=true || true
kubectl delete service --all --all-namespaces --field-selector spec.type=LoadBalancer --wait=true || true

echo "Removing PVCs (this deletes dynamically provisioned EBS volumes)..."
kubectl delete pvc --all --all-namespaces --wait=true || true

echo "Removing Argo CD Applications and Karpenter NodePools..."
kubectl -n argocd delete applications --all --wait=true || true
kubectl delete nodepool --all --wait=true || true

echo "Waiting for Karpenter-provisioned nodes to drain and terminate..."
for _ in $(seq 1 60); do
  COUNT="$(kubectl get nodes -l karpenter.sh/nodepool --no-headers 2>/dev/null | wc -l | tr -d ' ')"
  [[ "$COUNT" == "0" ]] && break
  sleep 10
done

REMAINING="$(kubectl get nodes -l karpenter.sh/nodepool --no-headers 2>/dev/null | wc -l | tr -d ' ')"
if [[ "$REMAINING" != "0" ]]; then
  echo "WARNING: ${REMAINING} Karpenter node(s) remain; inspect them before Terraform destroy." >&2
  exit 1
fi

echo "Kubernetes cleanup complete. Next, review and explicitly approve:"
echo "  terraform -chdir=terraform/platform destroy"
echo "  terraform -chdir=terraform/infra destroy"
echo "Keep terraform/bootstrap and ECR repositories unless a full cleanup is intended."
