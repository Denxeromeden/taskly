variable "name" { type = string }
variable "region" { type = string }
variable "audit_bucket_name" { type = string }
variable "audit_kms_key_arn" { type = string }
variable "tags" { type = map(string) }
variable "manage_guard_detector" {
  type        = bool
  description = "Set to true to create guard detector or false to look up an existing one"
  default     = false
}