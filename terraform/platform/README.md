# Platform stack

Consumes the infra stack through `terraform_remote_state` and installs pinned Helm charts plus
local foundation charts. Run only after the EKS cluster is ready and the caller can reach the
Kubernetes API. The apply environment must have the AWS CLI and `kubectl`: a Terraform
`local-exec` step installs the pinned Gateway API standard and AWS Load Balancer Controller
extension CRDs before the controller and `aws-alb` `GatewayClass` are installed.

The stack creates a self-signed ACM certificate when `acm_certificate_arn` is null; the private
key is consequently stored in encrypted Terraform state. Supply a trusted ACM certificate to
avoid browser warnings in real environments.

The default remote state bucket is derived from `<project>-<environment>-<account_id>-tfstate`,
matching `scripts/init-backends.sh`. An optional `state_bucket` override is available when the
bootstrap bucket name is customized. Copy `terraform.tfvars.example` to `terraform.tfvars` before
planning. The child Application under `deploy/argocd/` also contains the repository URL and branch;
update it alongside `argocd_repo_url` / `argocd_target_revision` when deploying a fork.
