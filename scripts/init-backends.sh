#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${AWS_REGION:-eu-central-1}"
PROJECT="${PROJECT:-mlflowops}"
ENVIRONMENT="${ENVIRONMENT:-demo}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${PROJECT}-${ENVIRONMENT}-${ACCOUNT_ID}-tfstate"
mkdir -p "${ROOT}/terraform/infra" "${ROOT}/terraform/platform"
for STACK in infra platform; do
  cat > "${ROOT}/terraform/${STACK}/backend.hcl" <<EOF
bucket       = "${BUCKET}"
key          = "${STACK}/terraform.tfstate"
region       = "${REGION}"
encrypt      = true
use_lockfile = true
EOF
done
printf 'Created backend config files in terraform/infra and terraform/platform.
'
printf 'Initialize with: terraform -chdir=terraform/infra init -backend-config=backend.hcl
'
printf 'Then: terraform -chdir=terraform/platform init -backend-config=backend.hcl
'
