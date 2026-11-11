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
bootstrap bucket name is customized.

Create a local, gitignored `terraform.tfvars` for this stack. Set `region` to the value returned
by `terraform -chdir=terraform/infra output -raw region` and `argocd_repo_url` to the Git repository
Argo CD should track. Neither value is hardcoded in the platform chart defaults or committed
Terraform inputs. The target revision defaults to `main` and can be overridden locally.

The `argocd-bootstrap` release passes the Git repository URL, the selected region, and the MLflow
ECR repository URI from infra remote state into the root Argo CD Application. The ECR URI and
region are held in Terraform/cluster configuration, not in Git-managed workload values. Argo CD
combines the ECR URI with the immutable image tag stored in
`deploy/charts/mlflow/values-image.yaml`; CI updates only that tag after image scans and ECR pushes
succeed.

For GitHub Actions, configure repository variable `AWS_REGION` to the value of
`terraform -chdir=terraform/infra output -raw region`, and repository secret `AWS_ROLE_ARN` to
the value of `terraform -chdir=terraform/infra output -raw github_actions_role_arn`. The workflow
uses OIDC; the role ARN is configuration, not a long-lived AWS credential.
