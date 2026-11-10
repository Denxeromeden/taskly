variable "region" {
  type    = string
  default = "eu-central-1"
}
variable "project" {
  type    = string
  default = "mlflowops"
}
variable "environment" {
  type    = string
  default = "demo"
}
variable "state_bucket" {
  type        = string
  description = "Optional override; defaults to <project>-<environment>-<account_id>-tfstate."
  default     = ""
}
variable "argocd_repo_url" {
  type    = string
  default = "https://github.com/Denxeromeden/taskly"
}
variable "argocd_target_revision" {
  type    = string
  default = "main"
}
variable "acm_certificate_arn" {
  type    = string
  default = null
}
variable "general_nodepool_cpu_limit" {
  type    = string
  default = "16"
}
variable "training_nodepool_cpu_limit" {
  type    = string
  default = "8"
}
