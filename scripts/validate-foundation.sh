#!/usr/bin/env bash
# Static validation only: never applies or destroys AWS resources.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

for binary in terraform tflint checkov; do
  command -v "$binary" >/dev/null 2>&1 || { echo "$binary is required" >&2; exit 2; }
done

terraform fmt -check -recursive terraform
for stack in bootstrap infra platform; do
  echo "==> Initializing ${stack} without a remote backend"
  terraform -chdir="terraform/${stack}" init -backend=false -input=false
  terraform -chdir="terraform/${stack}" validate
done

tflint --recursive
checkov --directory terraform --framework terraform
