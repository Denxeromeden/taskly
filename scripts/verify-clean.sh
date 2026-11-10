#!/usr/bin/env bash
# Run after scripts/teardown.sh and after the infra stack has been destroyed.
set -euo pipefail
REGION="${AWS_REGION:-eu-central-1}"
PROJECT="${PROJECT:-mlflowops}"
ENVIRONMENT="${ENVIRONMENT:-demo}"
CLUSTER="${CLUSTER_NAME:-${PROJECT}-${ENVIRONMENT}}"
FAIL=0

check_empty() {
  local label="$1"; shift
  local result
  result="$("$@" 2>/dev/null)" || { echo "ERROR ${label}: AWS query failed"; FAIL=$((FAIL + 1)); return; }
  if [[ -z "$result" || "$result" == "None" ]]; then
    echo "PASS  ${label}: none found"
  else
    echo "FAIL  ${label}: ${result}"
    FAIL=$((FAIL + 1))
  fi
}

check_empty 'project-tagged running/stopped EC2 instances' \
  aws ec2 describe-instances --region "$REGION" \
    --filters "Name=tag:Project,Values=${PROJECT}" "Name=tag:Env,Values=${ENVIRONMENT}" \
      'Name=instance-state-name,Values=pending,running,stopping,stopped' \
    --query 'Reservations[].Instances[].InstanceId' --output text
check_empty 'project-tagged EBS volumes' \
  aws ec2 describe-volumes --region "$REGION" \
    --filters "Name=tag:Project,Values=${PROJECT}" "Name=tag:Env,Values=${ENVIRONMENT}" \
    --query 'Volumes[].VolumeId' --output text
check_empty 'project-tagged network interfaces' \
  aws ec2 describe-network-interfaces --region "$REGION" \
    --filters "Name=tag:Project,Values=${PROJECT}" "Name=tag:Env,Values=${ENVIRONMENT}" \
    --query 'NetworkInterfaces[].NetworkInterfaceId' --output text
check_empty 'load balancers tagged for this cluster' \
  aws resourcegroupstaggingapi get-resources --region "$REGION" \
    --resource-type-filters elasticloadbalancing:loadbalancer \
    --tag-filters "Key=elbv2.k8s.aws/cluster,Values=${CLUSTER}" \
    --query 'ResourceTagMappingList[].ResourceARN' --output text

if (( FAIL > 0 )); then
  echo "Cleanup check failed: ${FAIL} resource group(s) remain or could not be queried."
  exit 1
fi
echo 'Cleanup check passed. This checks project-tagged EC2 resources and cluster-tagged load balancers; inspect AWS billing/resource inventory for untagged leftovers.'
