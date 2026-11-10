variable "name" { type = string }
variable "region" { type = string }
variable "cluster_name" { type = string }
variable "cluster_arn" { type = string }
variable "github_owner" { type = string }
variable "github_repo" { type = string }
variable "github_owner_id" { type = string }
variable "github_repo_id" { type = string }
variable "artifacts_bucket_arn" { type = string }
variable "rds_master_secret_arn" { type = string }
variable "data_kms_key_arn" { type = string }
variable "tags" { type = map(string) }
variable "manage_oidc_provider" {
  type        = bool
  description = "Set to true to create the provider or false to look up an existing one"
  default     = false
}