variable "project" {
  type    = string
  default = "mlflowops"
}
variable "environment" {
  type    = string
  default = "demo"
}
variable "region" {
  type    = string
  default = "eu-central-1"
}
variable "owner" {
  type    = string
  default = "taskly"
}
variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}
variable "kubernetes_version" {
  type    = string
  default = "1.35"
}
variable "eks_public_access_cidrs" {
  type        = list(string)
  description = "CIDRs permitted to reach the public EKS API endpoint; default is intentionally broad for the demo."
  default     = ["0.0.0.0/0"]
}
variable "admin_principal_arns" {
  type        = list(string)
  description = "IAM users or SSO role ARNs granted EKS cluster-admin through EKS access entries."
}
variable "github_owner" {
  type = string
}
variable "github_repo" {
  type    = string
  default = "taskly"
}
variable "github_owner_id" {
  type = string
}
variable "github_repo_id" {
  type = string
}
variable "rds_instance_class" {
  type    = string
  default = "db.t4g.small"
}
variable "base_node_instance_type" {
  type    = string
  default = "m6i.large"
}
variable "rotation_app_version" {
  type    = string
  default = "1.1.387"
}
variable "enable_cluster_creator_owner" {
  type    = bool
  default = true
}

