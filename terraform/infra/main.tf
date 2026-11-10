data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
locals {
  name           = "${var.project}-${var.environment}"
  tags           = { Project = var.project, Env = var.environment, Owner = var.owner, ManagedBy = "Terraform" }
  cloudtrail_arn = "arn:${data.aws_partition.current.partition}:cloudtrail:${var.region}:${data.aws_caller_identity.current.account_id}:trail/${local.name}-trail"
}
module "kms" {
  source         = "../modules/kms"
  name           = local.name
  region         = var.region
  tags           = local.tags
  cloudtrail_arn = local.cloudtrail_arn
}
module "network" {
  source   = "../modules/network"
  name     = local.name
  region   = var.region
  vpc_cidr = var.vpc_cidr
  tags     = local.tags
}
module "eks" {
  source                       = "../modules/eks"
  name                         = local.name
  region                       = var.region
  kubernetes_version           = var.kubernetes_version
  public_access_cidrs          = var.eks_public_access_cidrs
  admin_principal_arns         = var.admin_principal_arns
  eks_kms_key_arn              = module.kms.key_arns["eks"]
  vpc_id                       = module.network.vpc_id
  private_subnet_ids           = module.network.private_subnet_ids
  base_node_instance_type      = var.base_node_instance_type
  tags                         = local.tags
  enable_cluster_creator_owner = var.enable_cluster_creator_owner
}
module "data" {
  source                 = "../modules/data"
  name                   = local.name
  project                = var.project
  environment            = var.environment
  region                 = var.region
  vpc_id                 = module.network.vpc_id
  private_subnet_ids     = module.network.private_subnet_ids
  database_subnet_ids    = module.network.database_subnet_ids
  node_security_group_id = module.eks.node_security_group_id
  data_kms_key_arn       = module.kms.key_arns["data"]
  audit_kms_key_arn      = module.kms.key_arns["audit"]
  rds_instance_class     = var.rds_instance_class
  rotation_app_version   = var.rotation_app_version
  tags                   = local.tags
}
module "identity" {
  source                = "../modules/identity"
  name                  = local.name
  region                = var.region
  cluster_name          = module.eks.cluster_name
  cluster_arn           = module.eks.cluster_arn
  github_owner          = var.github_owner
  github_repo           = var.github_repo
  github_owner_id       = var.github_owner_id
  github_repo_id        = var.github_repo_id
  artifacts_bucket_arn  = module.data.artifacts_bucket_arn
  rds_master_secret_arn = module.data.rds_master_secret_arn
  data_kms_key_arn      = module.kms.key_arns["data"]
  tags                  = local.tags
}
module "security" {
  source            = "../modules/security"
  name              = local.name
  region            = var.region
  audit_bucket_name = module.data.audit_bucket_name
  audit_kms_key_arn = module.kms.key_arns["audit"]
  tags              = local.tags
}
