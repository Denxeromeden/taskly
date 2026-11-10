data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_region" "current" {}

resource "aws_guardduty_detector" "this" {
  count          = var.manage_guard_detector ? 1 : 0
  enable                       = true
  finding_publishing_frequency = "FIFTEEN_MINUTES"
  tags = var.tags
}

data "aws_guardduty_detector" "this" {
  count          = var.manage_guard_detector ? 0 : 1
}


resource "aws_guardduty_detector_feature" "s3_protection" {
  detector_id = var.manage_guard_detector ? aws_guardduty_detector.this[0].id : data.aws_guardduty_detector.this[0].id
  name        = "S3_DATA_EVENTS"
  status      = "ENABLED"
}

resource "aws_guardduty_detector_feature" "eks_audit_logs" {
  detector_id = var.manage_guard_detector ? aws_guardduty_detector.this[0].id : data.aws_guardduty_detector.this[0].id
  name        = "EKS_AUDIT_LOGS"
  status      = "ENABLED"
}

resource "aws_cloudwatch_log_group" "cloudtrail" {
  name              = "/aws/cloudtrail/${var.name}"
  retention_in_days = 30
  kms_key_id        = var.audit_kms_key_arn
  tags              = var.tags
}

resource "aws_iam_role" "cloudtrail_to_cloudwatch" {
  name = "${var.name}-cloudtrail-cloudwatch"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    # The role trust is limited to the AWS CloudTrail service principal.
    Statement = [{ Effect = "Allow", Principal = { Service = "cloudtrail.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
  tags = var.tags
}
resource "aws_iam_role_policy" "cloudtrail_to_cloudwatch" {
  name = "${var.name}-cloudtrail-cloudwatch"
  role = aws_iam_role.cloudtrail_to_cloudwatch.id
  policy = jsonencode({
    Version = "2012-10-17"
    # CloudTrail can create streams and write events only to its dedicated log group.
    Statement = [{
      Effect = "Allow"
      Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.cloudtrail.arn}:*"
    }]
  })
}

resource "aws_cloudtrail" "this" {
  name                          = "${var.name}-trail"
  s3_bucket_name                = var.audit_bucket_name
  s3_key_prefix                 = "cloudtrail"
  include_global_service_events = true
  is_multi_region_trail         = false
  enable_logging                = true
  enable_log_file_validation    = true
  kms_key_id                    = var.audit_kms_key_arn
  cloud_watch_logs_group_arn    = "${aws_cloudwatch_log_group.cloudtrail.arn}:*"
  cloud_watch_logs_role_arn     = aws_iam_role.cloudtrail_to_cloudwatch.arn
  event_selector {
    read_write_type           = "All"
    include_management_events = true
  }
  tags = var.tags
  depends_on = [aws_iam_role_policy.cloudtrail_to_cloudwatch]
}

resource "aws_glue_catalog_database" "audit" {
  name = "mlflowops_audit"
}
resource "aws_glue_catalog_table" "cloudtrail" {
  name          = "cloudtrail_logs"
  database_name = aws_glue_catalog_database.audit.name
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    EXTERNAL = "TRUE"
    "projection.enabled" = "true"
    "projection.timestamp.type" = "date"
    "projection.timestamp.format" = "yyyy/MM/dd"
    "projection.timestamp.range" = "2026/01/01,NOW"
    "projection.timestamp.interval" = "1"
    "projection.timestamp.interval.unit" = "DAYS"
    "storage.location.template" = "s3://${var.audit_bucket_name}/cloudtrail/AWSLogs/${data.aws_caller_identity.current.account_id}/CloudTrail/${var.region}/$${timestamp}/"
  }
  storage_descriptor {
    location      = "s3://${var.audit_bucket_name}/cloudtrail/AWSLogs/${data.aws_caller_identity.current.account_id}/CloudTrail/${var.region}/"
    input_format  = "com.amazon.emr.cloudtrail.CloudTrailInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
    ser_de_info { serialization_library = "com.amazon.emr.hive.serde.CloudTrailSerde" }
    columns {
      name = "eventtime"
      type = "string"
    }
    columns {
      name = "eventsource"
      type = "string"
    }
    columns {
      name = "eventname"
      type = "string"
    }
    columns {
      name = "awsregion"
      type = "string"
    }
    columns {
      name = "errorcode"
      type = "string"
    }
    columns {
      name = "useridentity"
      type = "struct<arn:string,accountid:string,principalid:string,type:string>"
    }
    columns {
      name = "requestparameters"
      type = "string"
    }
    columns {
      name = "responseelements"
      type = "string"
    }
  }
  partition_keys {
    name = "timestamp"
    type = "string"
  }
}
resource "aws_athena_workgroup" "this" {
  name = "mlflowops"
  configuration {
    enforce_workgroup_configuration = true
    result_configuration {
      output_location = "s3://${var.audit_bucket_name}/athena-results/"
      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn           = var.audit_kms_key_arn
      }
    }
  }
  tags = var.tags
}
resource "aws_athena_named_query" "denied_calls" {
  name        = "denied-calls-last-24h"
  description = "Denied AWS API calls in the previous 24 hours."
  workgroup   = aws_athena_workgroup.this.id
  database    = aws_glue_catalog_database.audit.name
  query       = <<-SQL
    SELECT eventtime, useridentity.arn, eventsource, eventname, errorcode
    FROM cloudtrail_logs
    WHERE errorcode IN ('AccessDenied', 'Client.UnauthorizedOperation')
      AND from_iso8601_timestamp(eventtime) >= current_timestamp - INTERVAL '24' HOUR
      AND "timestamp" >= date_format(current_date - INTERVAL '1' DAY, '%Y/%m/%d')
      AND "timestamp" <= date_format(current_date, '%Y/%m/%d')
    ORDER BY eventtime DESC;
  SQL
}
resource "aws_athena_named_query" "github_assumptions" {
  name        = "github-role-assumptions"
  description = "GitHub OIDC role assumptions grouped by day."
  workgroup   = aws_athena_workgroup.this.id
  database    = aws_glue_catalog_database.audit.name
  query       = <<-SQL
    SELECT date(from_iso8601_timestamp(eventtime)) AS day, useridentity.arn, count(*) AS assumptions
    FROM cloudtrail_logs
    WHERE eventname = 'AssumeRoleWithWebIdentity'
    GROUP BY 1, 2 ORDER BY 1 DESC;
  SQL
}

output "guardduty_detector_id" { value = var.manage_guard_detector ? aws_guardduty_detector.this[0].id : data.aws_guardduty_detector.this[0].id }
output "cloudtrail_arn" { value = aws_cloudtrail.this.arn }
output "athena_workgroup" { value = aws_athena_workgroup.this.name }
