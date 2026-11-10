data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

locals {
  artifacts_bucket = "${var.project}-${var.environment}-${data.aws_caller_identity.current.account_id}-mlflow-artifacts"
  audit_bucket     = "${var.project}-${var.environment}-${data.aws_caller_identity.current.account_id}-audit-logs"
  ecr_repositories = toset(["serving", "ml-jobs", "mlflow"])
  app_credentials = {
    mlflow = { username = "mlflow_app", dbname = "mlflow" }
    tasks  = { username = "tasks_app", dbname = "tasks" }
  }
}

resource "aws_s3_bucket" "artifacts" {
  bucket = local.artifacts_bucket
  tags   = merge(var.tags, { Name = local.artifacts_bucket })
}
resource "aws_s3_bucket" "audit" {
  bucket = local.audit_bucket
  tags   = merge(var.tags, { Name = local.audit_bucket })
}

resource "aws_s3_bucket_ownership_controls" "buckets" {
  for_each = { artifacts = aws_s3_bucket.artifacts, audit = aws_s3_bucket.audit }
  bucket   = each.value.id
  rule { object_ownership = "BucketOwnerEnforced" }
}
resource "aws_s3_bucket_public_access_block" "buckets" {
  for_each = { artifacts = aws_s3_bucket.artifacts, audit = aws_s3_bucket.audit }
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_versioning" "buckets" {
  for_each = { artifacts = aws_s3_bucket.artifacts, audit = aws_s3_bucket.audit }
  bucket   = each.value.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "buckets" {
  for_each = { artifacts = aws_s3_bucket.artifacts, audit = aws_s3_bucket.audit }
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = each.key == "audit" ? var.audit_kms_key_arn : var.data_kms_key_arn
    }
    bucket_key_enabled = true
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "expire-noncurrent-artifacts"
    status = "Enabled"
    filter { prefix = "" }
    noncurrent_version_expiration { noncurrent_days = 30 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "audit" {
  bucket = aws_s3_bucket.audit.id
  rule {
    id     = "expire-audit-objects"
    status = "Enabled"
    filter { prefix = "" }
    expiration { days = 30 }
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
}
# Artifacts bucket: force TLS for every read/write request.
resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

# Audit bucket: force TLS and grant only CloudTrail's bucket check and account log writes.
resource "aws_s3_bucket_policy" "audit" {
  bucket = aws_s3_bucket.audit.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # Deny any request that does not use TLS to access the audit bucket or its objects.
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.audit.arn, "${aws_s3_bucket.audit.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      # CloudTrail validates this exact bucket before it begins log delivery.
      {
        Sid       = "AllowCloudTrailBucketAclCheck"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.audit.arn
        Condition = { StringEquals = { "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:cloudtrail:${var.region}:${data.aws_caller_identity.current.account_id}:trail/${var.name}-trail" } }
      },
      # Allow CloudTrail to write only this account's log objects under the fixed prefix.
      {
        Sid       = "AllowCloudTrailWrite"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.audit.arn}/cloudtrail/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
            "aws:SourceArn" = "arn:${data.aws_partition.current.partition}:cloudtrail:${var.region}:${data.aws_caller_identity.current.account_id}:trail/${var.name}-trail"
          }
        }
      }
    ]
  })
}

resource "aws_ecr_repository" "this" {
  for_each             = local.ecr_repositories
  name                 = "mlflowops/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key          = var.data_kms_key_arn
  }
  tags = merge(var.tags, { Name = "mlflowops/${each.key}" })
}
resource "aws_ecr_lifecycle_policy" "this" {
  for_each   = aws_ecr_repository.this
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the most recent 20 immutable image tags"
      selection = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action    = { type = "expire" }
    }]
  })
}

resource "aws_security_group" "rotation_lambda" {
  name        = "${var.name}-rotation-lambda"
  description = "RDS credential rotation Lambda network access"
  vpc_id      = var.vpc_id
  tags = var.tags
}
resource "aws_vpc_security_group_egress_rule" "rotation_to_rds" {
  security_group_id            = aws_security_group.rotation_lambda.id
  referenced_security_group_id = aws_security_group.rds.id

  ip_protocol = "tcp"
  from_port   = 5432
  to_port     = 5432
}
resource "aws_vpc_security_group_egress_rule" "secrets_manager_https" {
  security_group_id            = aws_security_group.rotation_lambda.id
  cidr_ipv4         = "0.0.0.0/0"

  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443
}
resource "aws_security_group" "rds" {
  name        = "${var.name}-rds"
  description = "PostgreSQL only from EKS nodes and rotation Lambda"
  vpc_id      = var.vpc_id
  # No egress is required for an RDS instance in this design.
  egress = []
  tags   = var.tags
}
resource "aws_vpc_security_group_ingress_rule" "rds_nodes" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = var.node_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL from the shared EKS node security group"
}
resource "aws_vpc_security_group_ingress_rule" "rds_rotation" {
  security_group_id            = aws_security_group.rds.id
  referenced_security_group_id = aws_security_group.rotation_lambda.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  description                  = "PostgreSQL from the password-rotation Lambda"
}

resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-database"
  subnet_ids = var.database_subnet_ids
  tags       = var.tags
}
resource "aws_db_parameter_group" "this" {
  name   = "${var.name}-postgres16"
  family = "postgres16"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  tags = var.tags
}
resource "aws_db_instance" "this" {
  identifier                  = var.name
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = var.rds_instance_class
  allocated_storage           = 20
  max_allocated_storage       = 50
  storage_type                = "gp3"
  storage_encrypted           = true
  kms_key_id                  = var.data_kms_key_arn
  db_name                     = null
  username                    = "mlflowops_admin"
  manage_master_user_password = true
  master_user_secret_kms_key_id = var.data_kms_key_arn
  db_subnet_group_name        = aws_db_subnet_group.this.name
  vpc_security_group_ids      = [aws_security_group.rds.id]
  parameter_group_name        = aws_db_parameter_group.this.name
  publicly_accessible         = false
  multi_az                    = false
  backup_retention_period     = 7
  auto_minor_version_upgrade  = true
  apply_immediately           = false
  skip_final_snapshot         = true
  deletion_protection         = false
  copy_tags_to_snapshot       = true
  tags                        = var.tags
}

resource "random_password" "app" {
  for_each         = local.app_credentials
  length           = 32
  special          = true
  override_special = "!#$%&()-_=+[]{}<>:?"
}
resource "aws_secretsmanager_secret" "app" {
  for_each   = local.app_credentials
  name       = "mlflowops/rds/${each.key}"
  kms_key_id = var.data_kms_key_arn
  tags       = var.tags
}
resource "aws_secretsmanager_secret_version" "app" {
  for_each      = local.app_credentials
  secret_id     = aws_secretsmanager_secret.app[each.key].id
  secret_string = jsonencode({
    engine   = "postgres"
    host     = aws_db_instance.this.address
    port     = aws_db_instance.this.port
    dbname   = each.value.dbname
    username = each.value.username
    password = random_password.app[each.key].result
  })
  lifecycle { ignore_changes = [secret_string] }
}

# AWS's maintained single-user PostgreSQL rotation app is deployed from SAR (published only in us-east-1).
# The app creates a Lambda function whose ARN is consumed by both secret rotation schedules.
resource "aws_serverlessapplicationrepository_cloudformation_stack" "rotation" {
  name           = "${var.name}-postgres-rotation"
  semantic_version = var.rotation_app_version
  application_id = "arn:aws:serverlessrepo:us-east-1:297356227824:applications/SecretsManagerRDSPostgreSQLRotationSingleUser"
  capabilities   = ["CAPABILITY_IAM", "CAPABILITY_RESOURCE_POLICY"]
  parameters = {
    functionName         = "${var.name}-postgres-rotation"
    endpoint             = "https://secretsmanager.${var.region}.${data.aws_partition.current.dns_suffix}"
    kmsKeyArn            = var.data_kms_key_arn
    vpcSecurityGroupIds  = aws_security_group.rotation_lambda.id
    vpcSubnetIds         = join(",", var.private_subnet_ids)
  }
  tags = var.tags
  depends_on = [aws_db_instance.this, aws_secretsmanager_secret_version.app]
}

data "aws_lambda_function" "rotation" {
  function_name = "${var.name}-postgres-rotation"
  depends_on    = [aws_serverlessapplicationrepository_cloudformation_stack.rotation]
}

resource "aws_lambda_permission" "rotation" {
  for_each      = aws_secretsmanager_secret.app
  statement_id  = "AllowSecretsManagerRotation-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = data.aws_lambda_function.rotation.function_name
  principal     = "secretsmanager.amazonaws.com"
  source_account = data.aws_caller_identity.current.account_id
  source_arn     = each.value.arn
}

resource "aws_secretsmanager_secret_rotation" "app" {
  for_each            = aws_secretsmanager_secret.app
  secret_id           = each.value.id
  rotation_lambda_arn = data.aws_lambda_function.rotation.arn
  rotation_rules {
    automatically_after_days = 30
  }
  depends_on = [aws_lambda_permission.rotation]
}

output "artifacts_bucket_name" { value = aws_s3_bucket.artifacts.bucket }
output "artifacts_bucket_arn" { value = aws_s3_bucket.artifacts.arn }
output "audit_bucket_name" { value = aws_s3_bucket.audit.bucket }
output "audit_bucket_arn" { value = aws_s3_bucket.audit.arn }
output "rds_endpoint" { value = aws_db_instance.this.address }
output "rds_port" { value = aws_db_instance.this.port }
output "rds_master_secret_arn" { value = aws_db_instance.this.master_user_secret[0].secret_arn }
output "app_secret_arns" { value = { for k, v in aws_secretsmanager_secret.app : k => v.arn } }
output "ecr_repository_urls" { value = { for k, v in aws_ecr_repository.this : k => v.repository_url } }
output "rds_security_group_id" { value = aws_security_group.rds.id }
output "rotation_lambda_security_group_id" { value = aws_security_group.rotation_lambda.id }
