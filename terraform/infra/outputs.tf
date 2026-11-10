output "region" { value = var.region }
output "account_id" { value = data.aws_caller_identity.current.account_id }
output "cluster_name" { value = module.eks.cluster_name }
output "cluster_arn" { value = module.eks.cluster_arn }
output "cluster_endpoint" { value = module.eks.cluster_endpoint }
output "cluster_certificate_authority_data" { value = module.eks.cluster_certificate_authority_data }
output "vpc_id" { value = module.network.vpc_id }
output "public_subnet_ids" { value = module.network.public_subnet_ids }
output "private_subnet_ids" { value = module.network.private_subnet_ids }
output "database_subnet_ids" { value = module.network.database_subnet_ids }
output "node_security_group_id" { value = module.eks.node_security_group_id }
output "karpenter_controller_role_arn" { value = module.eks.karpenter_controller_role_arn }
output "karpenter_node_role_name" { value = module.eks.karpenter_node_role_name }
output "karpenter_queue_name" { value = module.eks.karpenter_queue_name }
output "artifacts_bucket_name" { value = module.data.artifacts_bucket_name }
output "artifacts_bucket_arn" { value = module.data.artifacts_bucket_arn }
output "audit_bucket_name" { value = module.data.audit_bucket_name }
output "ecr_repository_urls" { value = module.data.ecr_repository_urls }
output "rds_endpoint" { value = module.data.rds_endpoint }
output "rds_port" { value = module.data.rds_port }
output "rds_master_secret_arn" {
  value     = module.data.rds_master_secret_arn
  sensitive = true
}
output "app_secret_arns" {
  value     = module.data.app_secret_arns
  sensitive = true
}
output "github_actions_role_arn" { value = module.identity.github_actions_role_arn }
output "github_actions_role_name" { value = module.identity.github_actions_role_name }
output "pod_identity_role_arns" { value = module.identity.pod_identity_role_arns }
output "guardduty_detector_id" { value = module.security.guardduty_detector_id }
output "cloudtrail_arn" { value = module.security.cloudtrail_arn }
output "athena_workgroup" { value = module.security.athena_workgroup }
