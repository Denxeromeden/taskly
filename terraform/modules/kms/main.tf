data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  aliases = toset(["data", "eks", "audit"])
}

resource "aws_kms_key" "this" {
  for_each                = local.aliases
  description             = "${var.name} ${each.key} encryption key"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = each.key == "audit" ? data.aws_iam_policy_document.audit[0].json : data.aws_iam_policy_document.account_root.json
  tags   = var.tags
}

resource "aws_kms_alias" "this" {
  for_each      = aws_kms_key.this
  name          = "alias/${var.name}-${each.key}"
  target_key_id = each.value.key_id
}

data "aws_iam_policy_document" "account_root" {
  # Standard KMS key-policy delegation: the account root principal enables IAM policies in this account to grant scoped use.
  statement {
    sid       = "EnableAccountIAMPermissions"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }
}

data "aws_iam_policy_document" "audit" {
  count = 1
  source_policy_documents = [data.aws_iam_policy_document.account_root.json]
  # CloudTrail may generate data keys only when writing this specific trail.
  statement {
    sid       = "AllowCloudTrailToEncryptThisTrail"
    effect    = "Allow"
    actions   = ["kms:GenerateDataKey*", "kms:DescribeKey"]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = [var.cloudtrail_arn]
    }
  }
  # CloudWatch Logs needs direct key access for the dedicated CloudTrail log group.
  statement {
    sid    = "AllowCloudWatchLogsForCloudTrailLogGroup"
    effect = "Allow"
    actions = [
      "kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*",
      "kms:GenerateDataKey*", "kms:Describe*"
    ]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }
    condition {
      test     = "ArnEquals"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:${data.aws_partition.current.partition}:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/cloudtrail/${var.name}"]
    }
  }
}

output "key_arns" { value = { for k, v in aws_kms_key.this : k => v.arn } }
output "key_ids"  { value = { for k, v in aws_kms_key.this : k => v.key_id } }
