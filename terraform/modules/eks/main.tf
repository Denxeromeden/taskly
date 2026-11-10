data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "pod_identity_assume" {
  statement {
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
    condition {
      test     = "ArnEquals"
      variable = "aws:SourceArn"
      values   = ["arn:${data.aws_partition.current.partition}:eks:${var.region}:${data.aws_caller_identity.current.account_id}:cluster/${var.name}"]
    }
  }
}

resource "aws_iam_role" "ebs_csi" {
  name               = "${var.name}-ebs-csi"
  assume_role_policy = data.aws_iam_policy_document.pod_identity_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.29.0"

  name               = var.name
  kubernetes_version = var.kubernetes_version
  authentication_mode = "API"

  endpoint_private_access      = true
  endpoint_public_access       = true
  endpoint_public_access_cidrs = var.public_access_cidrs

  enable_cluster_creator_admin_permissions = var.enable_cluster_creator_owner
  access_entries = {
    for idx, arn in var.admin_principal_arns : "admin-${idx}" => {
      principal_arn = arn
      policy_associations = {
        cluster_admin = {
          policy_arn = "arn:${data.aws_partition.current.partition}:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = { type = "cluster" }
        }
      }
    }
  }

  encryption_config = {
    provider_key_arn = var.eks_kms_key_arn
    resources        = ["secrets"]
  }
  enabled_log_types                   = ["api", "audit", "authenticator"]
  cloudwatch_log_group_retention_in_days = 14

  vpc_id                   = var.vpc_id
  subnet_ids               = var.private_subnet_ids
  control_plane_subnet_ids = var.private_subnet_ids

  addons = {
    vpc-cni = {
      before_compute       = true
      most_recent          = true
      configuration_values = jsonencode({ enableNetworkPolicy = "true" })
    }
    coredns = { most_recent = true }
    kube-proxy = { most_recent = true }
    eks-pod-identity-agent = {
      before_compute = true
      most_recent    = true
    }
    aws-ebs-csi-driver = {
      most_recent = true
      pod_identity_association = [{ service_account = "ebs-csi-controller-sa", role_arn = aws_iam_role.ebs_csi.arn }]
    }
  }

  eks_managed_node_groups = {
    base = {
      name           = "base"
      instance_types = [var.base_node_instance_type]
      capacity_type  = "ON_DEMAND"
      ami_type       = "AL2023_x86_64_STANDARD"
      min_size       = 2
      desired_size   = 2
      max_size       = 3
      subnet_ids     = var.private_subnet_ids
      labels         = { workload = "base" }
      metadata_options = {
        http_endpoint               = "enabled"
        http_tokens                 = "required"
        http_put_response_hop_limit = 1
      }
      block_device_mappings = {
        xvda = {
          device_name = "/dev/xvda"
          ebs = {
            volume_size           = 30
            volume_type           = "gp3"
            encrypted             = true
            delete_on_termination = true
          }
        }
      }
      update_config = { max_unavailable = 1 }
    }
  }

  node_security_group_tags = merge(var.tags, { "karpenter.sh/discovery" = var.name })
  tags = var.tags
}

module "karpenter" {
  source  = "terraform-aws-modules/eks/aws//modules/karpenter"
  version = "21.29.0"

  cluster_name                       = module.eks.cluster_name
  namespace                          = "kube-system"
  service_account                    = "karpenter"
  create_pod_identity_association     = true
  create_instance_profile             = false
  enable_inline_policy = true
  node_iam_role_name                  = "${var.name}-karpenter-node"
  node_iam_role_use_name_prefix       = false
  node_iam_role_additional_policies = {
    ssm = "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
  }
  tags = var.tags
}

# Avoid a create failure in accounts where the Spot service-linked role already exists.
data "aws_iam_roles" "spot_service_linked" {
  name_regex = "^AWSServiceRoleForEC2Spot$"
  path_prefix = "/aws-service-role/spot.amazonaws.com/"
}

resource "aws_iam_service_linked_role" "spot" {
  count            = length(data.aws_iam_roles.spot_service_linked.names) == 0 ? 1 : 0
  aws_service_name = "spot.amazonaws.com"
  description      = "Allows EC2 Spot to call AWS services on behalf of this account."
}

output "cluster_name" { value = module.eks.cluster_name }
output "cluster_arn" { value = module.eks.cluster_arn }
output "cluster_endpoint" { value = module.eks.cluster_endpoint }
output "cluster_certificate_authority_data" { value = module.eks.cluster_certificate_authority_data }
output "node_security_group_id" { value = module.eks.node_security_group_id }
output "karpenter_controller_role_arn" { value = module.karpenter.iam_role_arn }
output "karpenter_node_role_name" { value = module.karpenter.node_iam_role_name }
output "karpenter_node_role_arn" { value = module.karpenter.node_iam_role_arn }
output "karpenter_queue_name" { value = module.karpenter.queue_name }
