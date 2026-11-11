variable "region" {
  type        = string
  description = "AWS region used by the existing infra stack; set from its Terraform output in local terraform.tfvars."
  nullable    = false
  validation {
    condition     = length(trimspace(var.region)) > 0
    error_message = "region must match the region used by the infra stack."
  }
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
  type        = string
  description = "Git repository URL consumed by Argo CD; set in the local, gitignored terraform.tfvars."
  nullable    = false
  validation {
    condition     = length(trimspace(var.argocd_repo_url)) > 0
    error_message = "argocd_repo_url must be set to the Git repository Argo CD should track."
  }
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
