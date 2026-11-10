variable "region" { type = string }
variable "name" { type = string }
variable "kubernetes_version" { type = string }
variable "public_access_cidrs" { type = list(string) }
variable "admin_principal_arns" { type = list(string) }
variable "eks_kms_key_arn" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "base_node_instance_type" { type = string }
variable "tags" { type = map(string) }
variable "enable_cluster_creator_owner" { type = bool }

