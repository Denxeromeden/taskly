data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

locals {
  cluster_arn           = var.cluster_arn
  github_subject_prefix = "repo:${var.github_owner}@${var.github_owner_id}/${var.github_repo}@${var.github_repo_id}"
  repo_subjects = [
    "${local.github_subject_prefix}:ref:refs/heads/main",
    "${local.github_subject_prefix}:environment:staging",
    "${local.github_subject_prefix}:environment:production"
  ]
  ecr_arns = [for name in ["serving", "ml-jobs", "mlflow"] : "arn:${data.aws_partition.current.partition}:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/mlflowops/${name}"]
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.manage_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  tags           = var.tags
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.manage_oidc_provider ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}
data "aws_iam_policy_document" "github_trust" {
  # Only GitHub-issued web identity tokens may assume the CI role; aud/sub conditions below restrict it to this repository and approved refs/environments.
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [var.manage_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = local.repo_subjects
    }
  }
}
resource "aws_iam_role" "github_actions" {
  name               = "${var.name}-gha-ci"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
  tags               = var.tags
}
data "aws_iam_policy_document" "github_permissions" {
  # ECR GetAuthorizationToken does not support resource-level scoping; repository actions below are scoped.
  statement {
    sid       = "ECRAuthenticationToken"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  # Image data-plane permissions are limited to the three project repositories.
  statement {
    sid = "PushAndPullProjectImages"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
      "ecr:DescribeImages", "ecr:DescribeRepositories", "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload", "ecr:ListImages", "ecr:PutImage", "ecr:UploadLayerPart"
    ]
    resources = local.ecr_arns
  }
  # The CI client needs the single cluster endpoint/CA to configure kubectl.
  statement {
    sid       = "DescribeTheProjectCluster"
    actions   = ["eks:DescribeCluster"]
    resources = [local.cluster_arn]
  }
}
resource "aws_eks_access_entry" "github_actions" {
  cluster_name      = var.cluster_name
  principal_arn     = aws_iam_role.github_actions.arn
  kubernetes_groups = ["mlflowops:deployers"]
  type              = "STANDARD"
}

resource "aws_iam_role_policy" "github_actions" {
  name   = "${var.name}-gha-ecr-and-eks-describe"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_permissions.json
}

# EKS Pod Identity trust is shared by application roles; associations bind each role to one service account.
resource "aws_iam_role" "external_secrets" {
  name               = "${var.name}-external-secrets"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_assume.json
  tags               = var.tags
}
resource "aws_iam_role" "mlflow" {
  name               = "${var.name}-mlflow"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_assume.json
  tags               = var.tags
}
resource "aws_iam_role" "drift" {
  name               = "${var.name}-drift"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_assume.json
  tags               = var.tags
}
resource "aws_iam_role" "lb_controller" {
  name               = "${var.name}-lb-controller"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_assume.json
  tags               = var.tags
}
data "aws_iam_policy_document" "pod_identity_assume" {
  # Permit the EKS Pod Identity service only for this account and this EKS cluster.
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [var.cluster_arn]
    }
  }
}

resource "aws_iam_role_policy" "external_secrets" {
  name = "${var.name}-read-runtime-secrets"
  role = aws_iam_role.external_secrets.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ESO reads only app secrets under the project prefix and the RDS-managed master secret.
      { Sid = "ReadOnlyProjectSecrets", Effect = "Allow", Action = ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"], Resource = concat(["arn:${data.aws_partition.current.partition}:secretsmanager:${var.region}:${data.aws_caller_identity.current.account_id}:secret:mlflowops/*"], [var.rds_master_secret_arn]) },
      # Decrypt permission is restricted to the customer-managed data key used by those secrets.
      { Sid = "DecryptSecretValues", Effect = "Allow", Action = ["kms:Decrypt"], Resource = [var.data_kms_key_arn] }
    ]
  })
}
resource "aws_iam_role_policy" "mlflow" {
  name = "${var.name}-mlflow-artifacts"
  role = aws_iam_role.mlflow.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Bucket listing is restricted by s3:prefix to the MLflow artifact namespace.
      { Sid = "ListOnlyMlflowPrefix", Effect = "Allow", Action = ["s3:ListBucket"], Resource = [var.artifacts_bucket_arn], Condition = { StringLike = { "s3:prefix" = ["mlflow", "mlflow/*"] } } },
      # Object operations can touch only artifacts under mlflow/.
      { Sid = "ManageMlflowArtifacts", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"], Resource = ["${var.artifacts_bucket_arn}/mlflow/*"] },
      # Client-side S3 encryption operations use the project data key only.
      { Sid = "UseDataEncryptionKey", Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = [var.data_kms_key_arn] }
    ]
  })
}
resource "aws_iam_role_policy" "drift" {
  name = "${var.name}-drift-reports"
  role = aws_iam_role.drift.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Drift report list calls are restricted to drift-reports/.
      { Sid = "ListOnlyDriftPrefix", Effect = "Allow", Action = ["s3:ListBucket"], Resource = [var.artifacts_bucket_arn], Condition = { StringLike = { "s3:prefix" = ["drift-reports", "drift-reports/*"] } } },
      # Drift jobs cannot read or write MLflow model artifacts.
      { Sid = "ManageDriftReports", Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = ["${var.artifacts_bucket_arn}/drift-reports/*"] },
      # Drift reports use the same data key but remain isolated by S3 object prefix.
      { Sid = "UseDataEncryptionKey", Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = [var.data_kms_key_arn] }
    ]
  })
}

# The upstream AWS Load Balancer Controller policy contains wildcard resources for EC2/ELB create and describe APIs.
# Resource-level scoping is not supported by those APIs; request/resource tag conditions constrain mutations.
resource "aws_iam_role_policy" "lb_controller" {
  name   = "${var.name}-aws-load-balancer-controller"
  role   = aws_iam_role.lb_controller.id
  policy = file("${path.module}/lb-controller-policy.json")
}

resource "aws_eks_pod_identity_association" "external_secrets" {
  cluster_name    = var.cluster_name
  namespace       = "external-secrets"
  service_account = "external-secrets"
  role_arn        = aws_iam_role.external_secrets.arn
}
resource "aws_eks_pod_identity_association" "mlflow" {
  cluster_name    = var.cluster_name
  namespace       = "mlflow"
  service_account = "mlflow"
  role_arn        = aws_iam_role.mlflow.arn
}
resource "aws_eks_pod_identity_association" "drift" {
  cluster_name    = var.cluster_name
  namespace       = "ml-jobs"
  service_account = "drift"
  role_arn        = aws_iam_role.drift.arn
}
resource "aws_eks_pod_identity_association" "lb_controller" {
  cluster_name    = var.cluster_name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  role_arn        = aws_iam_role.lb_controller.arn
}

output "github_actions_role_arn" { value = aws_iam_role.github_actions.arn }
output "github_actions_role_name" { value = aws_iam_role.github_actions.name }
output "pod_identity_role_arns" {
  value = {
    external_secrets = aws_iam_role.external_secrets.arn
    mlflow           = aws_iam_role.mlflow.arn
    drift            = aws_iam_role.drift.arn
    lb_controller    = aws_iam_role.lb_controller.arn
  }
}
