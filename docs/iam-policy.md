# IAM policy rationale

Every hand-authored IAM statement in Terraform has a matching rationale below. The AWS-managed
EKS/EBS policies and the Karpenter module's upstream policies are deliberately delegated to their
maintainers rather than copied into this repository.

## Terraform bootstrap and KMS

- `DenyInsecureTransport` on the Terraform state bucket denies every S3 operation unless the
  request uses TLS. Bucket access is otherwise governed by the caller's IAM permissions.
- `EnableAccountIAMPermissions` is the standard KMS key-policy delegation statement: the account
  root principal enables IAM policies in this account to grant scoped usage of the key. It is not
  an application role grant by itself.
- `AllowCloudTrailToEncryptThisTrail` permits the CloudTrail service to generate data keys and
  describe the audit key only for this trail's source ARN.
- `AllowCloudWatchLogsForCloudTrailLogGroup` permits the regional CloudWatch Logs service to use
  the audit key only with the encryption context of the dedicated CloudTrail log group.

## GitHub Actions

- The OIDC trust statement accepts only the GitHub Actions OIDC provider, `aud=sts.amazonaws.com`,
  and the exact repository subjects for `main`, `staging`, and `production`. It intentionally
  excludes pull-request subjects and arbitrary branches.
- `ECRAuthenticationToken` uses `Resource: "*"` because `ecr:GetAuthorizationToken` does not support
  resource-level authorization.
- `PushAndPullProjectImages` scopes ECR image push/pull actions to the three `mlflowops/*`
  repositories.
- `DescribeTheProjectCluster` allows only `eks:DescribeCluster` on this EKS cluster. The EKS access
  entry maps the role to a custom Kubernetes group; it does not grant an EKS edit/cluster policy.

## EKS Pod Identity roles

- `pod_identity_assume` trusts only `pods.eks.amazonaws.com`, with `aws:SourceAccount` and
  `aws:SourceArn` restricted to this account and cluster. Explicit EKS Pod Identity associations
  bind each role to a namespace/service-account pair.
- `ReadOnlyProjectSecrets` and `DecryptSecretValues` allow External Secrets to read project secrets
  (including the RDS-managed master secret) and decrypt them with the data key. The role cannot
  write secrets or read unrelated Secrets Manager paths.
- `ListOnlyMlflowPrefix` and `ManageMlflowArtifacts` restrict the MLflow role to the `mlflow/`
  bucket prefix. `UseDataEncryptionKey` allows only the KMS operations required for SSE-KMS objects.
- `ListOnlyDriftPrefix` and `ManageDriftReports` restrict the drift role to `drift-reports/`;
  it cannot read model artifacts under `mlflow/`. Its KMS access is limited to the data key.
- No Pod Identity association is created for serving, training, seed, add-data, or db-bootstrap
  service accounts. Those workloads must not receive AWS credentials.

## AWS Load Balancer Controller

`terraform/modules/identity/lb-controller-policy.json` follows the upstream controller IAM policy
from the [AWS Load Balancer Controller v3.6.2 install documentation](https://raw.githubusercontent.com/kubernetes-sigs/aws-load-balancer-controller/v3.6.2/docs/install/iam_policy.json).
It includes optional certificate, WAF, Cognito, and Shield discovery/integration actions so normal
controller behavior does not fail when those features are enabled. Several EC2/ELB APIs do not
support resource-level authorization, so their upstream statements use `Resource: "*"`; mutation
statements use the controller's `elbv2.k8s.aws/cluster` request/resource tag conditions where AWS
supports them. Re-review this file against upstream documentation when upgrading the controller.

## CloudTrail

- The CloudTrail role trust statement permits only `cloudtrail.amazonaws.com` to assume the role.
- Its CloudWatch policy permits `logs:CreateLogStream` and `logs:PutLogEvents` only under the
  dedicated CloudTrail log group ARN.
- The audit bucket policy's CloudTrail statements allow a bucket ACL check and log-object writes
  only from the configured trail ARN and into this account's CloudTrail prefix.

## Upstream-managed policies

- The EBS CSI controller uses AWS's `AmazonEBSCSIDriverPolicy` managed policy, attached to its
  dedicated Pod Identity role.
- The Karpenter EKS submodule creates its controller/node roles, interruption queue, event rules,
  and the upstream-required EC2/SSM permissions. The controller role is Pod Identity-bound; the
  node role is referenced by the EC2NodeClass. Review the module's release notes on upgrades.


The principal creating the encrypted ECR repositories must also have `kms:CreateGrant`,
`kms:RetireGrant`, and `kms:DescribeKey` on the data key, in addition to ECR repository permissions.
AWS documents these as prerequisites for ECR repositories encrypted with a customer-managed key.

EKS Pod Identity trust policies are limited to the current AWS account and this cluster ARN
with `aws:SourceAccount` and `aws:SourceArn` conditions. The service-account associations then
restrict which Kubernetes identities receive each role.
