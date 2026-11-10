#!/usr/bin/env bash
# Build minimal non-production images and populate the three immutable ECR repositories.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REGION="${AWS_REGION:-eu-central-1}"
command -v aws >/dev/null || { echo 'aws CLI is required' >&2; exit 2; }
command -v docker >/dev/null || { echo 'docker is required' >&2; exit 2; }
ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

if [[ -n "${IMAGE_TAG:-}" ]]; then
  TAG="$IMAGE_TAG"
elif git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  TAG="$(git -C "$ROOT" rev-parse --short=12 HEAD)"
else
  TAG="$(sha256sum "$ROOT/images/placeholder/Dockerfile" | cut -c1-12)"
fi

if [[ "$TAG" == "latest" || "$TAG" == *[!a-zA-Z0-9_.-]* ]]; then
  echo "IMAGE_TAG must be an immutable, Docker-compatible tag (not 'latest')." >&2
  exit 2
fi

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"

for COMPONENT in serving ml-jobs mlflow; do
  REPOSITORY="mlflowops/${COMPONENT}"
  IMAGE="${REGISTRY}/${REPOSITORY}:${TAG}"

  if aws ecr describe-images --region "$REGION" --repository-name "$REPOSITORY" \
      --image-ids imageTag="$TAG" >/dev/null 2>&1; then
    echo "SKIP  ${REPOSITORY}:${TAG} already exists (ECR tags are immutable)."
    continue
  fi

  docker build \
    --file "$ROOT/images/placeholder/Dockerfile" \
    --build-arg "COMPONENT=${COMPONENT}" \
    --label "org.opencontainers.image.title=${REPOSITORY}" \
    --label "org.opencontainers.image.version=${TAG}" \
    --tag "$IMAGE" \
    "$ROOT/images/placeholder"
  docker push "$IMAGE"
  echo "PUSH  $IMAGE"
done

echo "Placeholder bootstrap complete. These images are not production workloads."
