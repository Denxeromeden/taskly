#!/usr/bin/env bash
# Build, scan and push the three workload images with immutable Git-SHA tags.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${AWS_REGION:?Set AWS_REGION to the region containing the existing ECR repositories.}"
REGION="$AWS_REGION"

for command in aws docker trivy; do
  command -v "$command" >/dev/null || {
    echo "$command is required" >&2
    exit 2
  }
done

if [[ -n "${IMAGE_TAG:-}" ]]; then
  TAG="$IMAGE_TAG"
elif git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  TAG="$(git -C "$ROOT" rev-parse HEAD)"
else
  echo "Run from a Git checkout or supply IMAGE_TAG as a full Git SHA." >&2
  exit 2
fi

if [[ ! "$TAG" =~ ^[0-9a-f]{40}$ ]]; then
  echo "IMAGE_TAG must be a full 40-character Git SHA." >&2
  exit 2
fi

ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

for COMPONENT in mlflow ml-jobs serving; do
  REPOSITORY="mlflowops/${COMPONENT}"
  DOCKERFILE="$ROOT/images/${COMPONENT}/Dockerfile"
  LOCAL_IMAGE="local/${REPOSITORY}:${TAG}"
  TARGET_IMAGE="${REGISTRY}/${REPOSITORY}:${TAG}"

  [[ -f "$DOCKERFILE" ]] || {
    echo "Missing Dockerfile: $DOCKERFILE" >&2
    exit 2
  }

  docker build --pull \
    --file "$DOCKERFILE" \
    --label "org.opencontainers.image.title=${REPOSITORY}" \
    --label "org.opencontainers.image.version=${TAG}" \
    --tag "$LOCAL_IMAGE" \
    "$ROOT"

  trivy image \
    --scanners vuln \
    --vuln-type os,library \
    --severity CRITICAL \
    --ignore-unfixed \
    --exit-code 1 \
    "$LOCAL_IMAGE"

  if aws ecr describe-images --region "$REGION" --repository-name "$REPOSITORY" \
      --image-ids "imageTag=${TAG}" >/dev/null 2>&1; then
    echo "SKIP  ${REPOSITORY}:${TAG} already exists (ECR tags are immutable)."
    continue
  fi

  docker tag "$LOCAL_IMAGE" "$TARGET_IMAGE"
  docker push "$TARGET_IMAGE"
  echo "PUSH  ${REPOSITORY}:${TAG}"
done
