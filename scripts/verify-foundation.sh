#!/usr/bin/env bash
# Non-destructive checks for foundation acceptance criteria after both stacks are applied.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PASS=0
FAIL=0

pass() { printf 'PASS  %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf 'FAIL  %s\n' "$1"; FAIL=$((FAIL + 1)); }
check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then pass "$label"; else fail "$label"; fi
}
check_eq() {
  local label="$1" expected="$2"; shift 2
  local actual
  actual="$("$@" 2>/dev/null)" || { fail "$label (command failed)"; return; }
  if [[ "$actual" == "$expected" ]]; then pass "$label"; else fail "$label (expected=$expected actual=$actual)"; fi
}
check_ge() {
  local label="$1" minimum="$2" actual
  actual="$3"
  if [[ "$actual" =~ ^[0-9]+$ ]] && (( actual >= minimum )); then pass "$label"; else fail "$label (expected >=$minimum actual=${actual:-unknown})"; fi
}
check_tls_policy() {
  local region="$1" bucket="$2" policy
  policy="$(aws s3api get-bucket-policy --region "$region" --bucket "$bucket" --query Policy --output text)" || return 1
  python3 -c 'import json,sys; p=json.loads(sys.argv[1]); print(str(any(s.get("Effect")=="Deny" and s.get("Condition",{}).get("Bool",{}).get("aws:SecureTransport") == "false" for s in p.get("Statement", []))).lower())' "$policy" | grep -qx true
}

for binary in aws kubectl terraform; do
  if ! command -v "$binary" >/dev/null 2>&1; then
    echo "$binary is required" >&2
    exit 2
  fi
done

CLUSTER="${CLUSTER_NAME:-mlflowops-demo}"
PROJECT="${PROJECT:-mlflowops}"
ENVIRONMENT="${ENVIRONMENT:-demo}"
DB_IDENTIFIER="${DB_IDENTIFIER:-${PROJECT}-${ENVIRONMENT}}"
REGION="${AWS_REGION:-eu-central-1}"

check_eq 'EKS cluster is ACTIVE' ACTIVE aws eks describe-cluster --region "$REGION" --name "$CLUSTER" --query 'cluster.status' --output text
check_eq 'EKS Kubernetes version is 1.35' 1.35 aws eks describe-cluster --region "$REGION" --name "$CLUSTER" --query 'cluster.version' --output text
check_eq 'EKS public API endpoint is enabled' True aws eks describe-cluster --region "$REGION" --name "$CLUSTER" --query 'cluster.resourcesVpcConfig.endpointPublicAccess' --output text
check_eq 'EKS private API endpoint is enabled' True aws eks describe-cluster --region "$REGION" --name "$CLUSTER" --query 'cluster.resourcesVpcConfig.endpointPrivateAccess' --output text

BASE_READY="$(kubectl get nodes -l workload=base -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' 2>/dev/null | awk '$0 == "True" {n++} END {print n+0}')"
check_ge 'at least two base nodes are Ready' 2 "$BASE_READY"
check 'Karpenter NodePools exist' kubectl get nodepools general-ondemand training-spot
check 'CoreDNS is ready' kubectl -n kube-system rollout status deployment/coredns --timeout=10s
check 'VPC CNI aws-node DaemonSet is ready' kubectl -n kube-system rollout status daemonset/aws-node --timeout=10s
check 'EKS Pod Identity Agent DaemonSet is ready' kubectl -n kube-system rollout status daemonset/eks-pod-identity-agent --timeout=10s
check 'Karpenter controller is ready' kubectl -n kube-system rollout status deployment/karpenter --timeout=10s
check 'AWS Load Balancer Controller is ready' kubectl -n kube-system rollout status deployment/aws-load-balancer-controller --timeout=10s
check 'Gateway API standard CRDs are installed' kubectl get crd gatewayclasses.gateway.networking.k8s.io gateways.gateway.networking.k8s.io httproutes.gateway.networking.k8s.io grpcroutes.gateway.networking.k8s.io
check 'AWS Load Balancer Controller Gateway API CRDs are installed' kubectl get crd loadbalancerconfigurations.gateway.k8s.aws targetgroupconfigurations.gateway.k8s.aws listenerruleconfigurations.gateway.k8s.aws
check 'aws-alb GatewayClass is accepted' bash -c 'test "$(kubectl get gatewayclass aws-alb -o jsonpath="{.status.conditions[?(@.type==\"Accepted\")].status}")" = True'
check 'metrics-server is ready' kubectl -n kube-system rollout status deployment/metrics-server --timeout=10s
check 'EBS CSI controller is ready' kubectl -n kube-system rollout status deployment/ebs-csi-controller --timeout=10s
check 'EBS CSI node DaemonSet is ready' kubectl -n kube-system rollout status daemonset/ebs-csi-node --timeout=10s
check 'External Secrets ClusterSecretStore is Ready' bash -c 'test "$(kubectl get clustersecretstore aws-secretsmanager -o jsonpath="{.status.conditions[?(@.type==\"Ready\")].status}")" = True'
check 'RDS master ExternalSecret is Ready' bash -c 'test "$(kubectl -n ml-jobs get externalsecret rds-master -o jsonpath="{.status.conditions[?(@.type==\"Ready\")].status}")" = True'
check 'MLflow credentials ExternalSecret is Ready' bash -c 'test "$(kubectl -n mlflow get externalsecret mlflow-db-credentials -o jsonpath="{.status.conditions[?(@.type==\"Ready\")].status}")" = True'
check 'Tasks credentials ExternalSecret is Ready' bash -c 'test "$(kubectl -n ml-jobs get externalsecret tasks-db-credentials -o jsonpath="{.status.conditions[?(@.type==\"Ready\")].status}")" = True'
check 'Argo CD root Application is Synced and Healthy' bash -c 'test "$(kubectl -n argocd get application mlflowops-root -o jsonpath="{.status.sync.status}")" = Synced && test "$(kubectl -n argocd get application mlflowops-root -o jsonpath="{.status.health.status}")" = Healthy'
check 'Argo CD database-bootstrap Application is Synced and Healthy' bash -c 'test "$(kubectl -n argocd get application db-bootstrap -o jsonpath="{.status.sync.status}")" = Synced && test "$(kubectl -n argocd get application db-bootstrap -o jsonpath="{.status.health.status}")" = Healthy'

ARTIFACTS_BUCKET="$(terraform -chdir="$ROOT/terraform/infra" output -raw artifacts_bucket_name 2>/dev/null || true)"
AUDIT_BUCKET="$(terraform -chdir="$ROOT/terraform/infra" output -raw audit_bucket_name 2>/dev/null || true)"
for BUCKET in "$ARTIFACTS_BUCKET" "$AUDIT_BUCKET"; do
  if [[ -z "$BUCKET" ]]; then
    fail 'Terraform artifact/audit bucket outputs are available'
    break
  fi
  check_eq "${BUCKET} all public access block settings are enabled" $'True\tTrue\tTrue\tTrue' aws s3api get-public-access-block --region "$REGION" --bucket "$BUCKET" --query 'PublicAccessBlockConfiguration.[BlockPublicAcls,IgnorePublicAcls,BlockPublicPolicy,RestrictPublicBuckets]' --output text
  check "${BUCKET} TLS-only bucket policy exists" check_tls_policy "$REGION" "$BUCKET"
  check_eq "${BUCKET} default encryption is SSE-KMS" aws:kms aws s3api get-bucket-encryption --region "$REGION" --bucket "$BUCKET" --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm' --output text
  check_eq "${BUCKET} versioning is enabled" Enabled aws s3api get-bucket-versioning --region "$REGION" --bucket "$BUCKET" --query Status --output text
done

check_eq 'RDS is not publicly accessible' False aws rds describe-db-instances --region "$REGION" --db-instance-identifier "$DB_IDENTIFIER" --query 'DBInstances[0].PubliclyAccessible' --output text
check_eq 'RDS storage encryption is enabled' True aws rds describe-db-instances --region "$REGION" --db-instance-identifier "$DB_IDENTIFIER" --query 'DBInstances[0].StorageEncrypted' --output text
check_eq 'RDS is in the isolated database subnet group' "${DB_IDENTIFIER}-database" aws rds describe-db-instances --region "$REGION" --db-instance-identifier "$DB_IDENTIFIER" --query 'DBInstances[0].DBSubnetGroup.DBSubnetGroupName' --output text

printf '\nResults: %s passed, %s failed.\n' "$PASS" "$FAIL"
(( FAIL == 0 ))
