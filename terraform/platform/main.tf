data "aws_caller_identity" "current" {}

data "terraform_remote_state" "infra" {
  backend = "s3"
  config = {
    bucket       = var.state_bucket != "" ? var.state_bucket : "${var.project}-${var.environment}-${data.aws_caller_identity.current.account_id}-tfstate"
    key          = "infra/terraform.tfstate"
    region       = var.region
    encrypt      = true
    use_lockfile = true
  }
}

locals {
  infra               = data.terraform_remote_state.infra.outputs
  name                = "${var.project}-${var.environment}"
  certificate_arn     = var.acm_certificate_arn != null ? var.acm_certificate_arn : aws_acm_certificate.self_signed[0].arn
  gateway_api_version = "v1.6.0"
  aws_lbc_version     = "3.6.2"

  platform_base_chart_path = "${path.module}/../../deploy/charts/platform-base"
  # The Helm provider does not detect edits to files inside a local chart path.
  # Feed a content hash as a no-op chart value so Terraform plans an in-place upgrade.
  platform_base_chart_files = sort(distinct(concat(
    tolist(fileset(local.platform_base_chart_path, "*")),
    tolist(fileset(local.platform_base_chart_path, "**/*"))
  )))
  platform_base_chart_checksum = sha256(join("\n", [
    for file in local.platform_base_chart_files :
    "${file}:${filesha256("${local.platform_base_chart_path}/${file}")}"
  ]))
}

# Install the Gateway API standard CRDs and the AWS Load Balancer Controller's
# Gateway API extension CRDs before enabling the controller or creating GatewayClass.
resource "terraform_data" "gateway_api_crds" {
  triggers_replace = {
    gateway_api_version = local.gateway_api_version
    aws_lbc_version     = local.aws_lbc_version
    installer_sha256    = filesha256("${path.module}/../../scripts/install-gateway-api-crds.sh")
  }

  provisioner "local-exec" {
    command = "bash ${path.module}/../../scripts/install-gateway-api-crds.sh"

    environment = {
      AWS_REGION          = var.region
      CLUSTER_NAME        = local.infra.cluster_name
      GATEWAY_API_VERSION = local.gateway_api_version
      AWS_LBC_VERSION     = local.aws_lbc_version
    }
  }
}
resource "tls_private_key" "self_signed" {
  count     = var.acm_certificate_arn == null ? 1 : 0
  algorithm = "RSA"
  rsa_bits  = 2048
}
resource "tls_self_signed_cert" "self_signed" {
  count                 = var.acm_certificate_arn == null ? 1 : 0
  private_key_pem       = tls_private_key.self_signed[0].private_key_pem
  validity_period_hours = 8760
  early_renewal_hours   = 168
  allowed_uses          = ["key_encipherment", "digital_signature", "server_auth"]
  subject { common_name = "mlflowops-demo.local" }
}
resource "aws_acm_certificate" "self_signed" {
  count            = var.acm_certificate_arn == null ? 1 : 0
  private_key      = tls_private_key.self_signed[0].private_key_pem
  certificate_body = tls_self_signed_cert.self_signed[0].cert_pem
  tags             = { Project = var.project, Env = var.environment, ManagedBy = "Terraform" }
}
resource "helm_release" "karpenter" {
  name       = "karpenter"
  namespace  = "kube-system"
  repository = "oci://public.ecr.aws/karpenter"
  chart      = "karpenter"
  version    = "1.15.1"
  wait       = true
  atomic     = true
  values = [yamlencode({
    settings       = { clusterName = local.infra.cluster_name, clusterEndpoint = local.infra.cluster_endpoint, interruptionQueue = local.infra.karpenter_queue_name }
    serviceAccount = { create = true, name = "karpenter" }
    # IMDS hop limit is 1 on every node; provide the region explicitly instead of querying metadata.
    env          = [{ name = "AWS_REGION", value = var.region }]
    replicas     = 2
    nodeSelector = { workload = "base" }
    resources    = { requests = { cpu = "500m", memory = "512Mi" }, limits = { cpu = "1", memory = "1Gi" } }
  })]
}
resource "helm_release" "karpenter_config" {
  name       = "karpenter-config"
  namespace  = "kube-system"
  chart      = "${path.module}/../../deploy/charts/karpenter-config"
  wait       = true
  depends_on = [helm_release.karpenter]
  set = [
    { name = "clusterName", value = local.infra.cluster_name },
    { name = "nodeRoleName", value = local.infra.karpenter_node_role_name },
    { name = "generalCpuLimit", value = var.general_nodepool_cpu_limit },
    { name = "trainingCpuLimit", value = var.training_nodepool_cpu_limit }
  ]
}
resource "helm_release" "load_balancer_controller" {
  name       = "aws-load-balancer-controller"
  namespace  = "kube-system"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = local.aws_lbc_version
  wait       = true
  atomic     = true
  values = [yamlencode({
    clusterName    = local.infra.cluster_name
    region         = var.region
    vpcId          = local.infra.vpc_id
    serviceAccount = { create = true, name = "aws-load-balancer-controller" }
    nodeSelector   = { workload = "base" }
    resources = {
      requests = { cpu = "100m", memory = "200Mi" }
      limits   = { cpu = "500m", memory = "512Mi" }
    }
    enableBackendSecurityGroup = true
    controllerConfig = {
      featureGates = { ALBGatewayAPI = true }
    }
  })]
  depends_on = [helm_release.karpenter_config, terraform_data.gateway_api_crds]
}
resource "helm_release" "external_secrets" {
  name             = "external-secrets"
  namespace        = "external-secrets"
  repository       = "https://charts.external-secrets.io"
  chart            = "external-secrets"
  version          = "2.14.0"
  create_namespace = true
  wait             = true
  atomic           = true
  values = [yamlencode({
    installCRDs    = true
    serviceAccount = { create = true, name = "external-secrets" }
    nodeSelector   = { workload = "base" }
    resources = {
      requests = { cpu = "50m", memory = "100Mi" }
      limits   = { cpu = "250m", memory = "256Mi" }
    }
    webhook = {
      nodeSelector = { workload = "base" }
      resources = {
        requests = { cpu = "25m", memory = "64Mi" }
        limits   = { cpu = "200m", memory = "128Mi" }
      }
    }
    certController = {
      nodeSelector = { workload = "base" }
      resources = {
        requests = { cpu = "25m", memory = "64Mi" }
        limits   = { cpu = "200m", memory = "128Mi" }
      }
    }
  })]
}
resource "helm_release" "reloader" {
  name       = "reloader"
  namespace  = "kube-system"
  repository = "https://stakater.github.io/stakater-charts"
  chart      = "reloader"
  version    = "2.2.19"
  wait       = true
  atomic     = true
  values = [yamlencode({
    reloader = {
      deployment = {
        nodeSelector = { workload = "base" }
        resources = {
          requests = { cpu = "10m", memory = "64Mi" }
          limits   = { cpu = "150m", memory = "128Mi" }
        }
      }
      readOnlyRootFileSystem = true
    }
  })]
}
resource "helm_release" "metrics_server" {
  name       = "metrics-server"
  namespace  = "kube-system"
  repository = "https://kubernetes-sigs.github.io/metrics-server/"
  chart      = "metrics-server"
  version    = "3.14.0"
  wait       = true
  atomic     = true
  values = [yamlencode({
    nodeSelector = { workload = "base" }
    resources = {
      requests = { cpu = "50m", memory = "100Mi" }
      limits   = { cpu = "250m", memory = "256Mi" }
    }
  })]
}
resource "helm_release" "platform_base" {
  name      = "platform-base"
  namespace = "default"
  chart     = local.platform_base_chart_path
  wait      = true
  atomic    = true
  values = [yamlencode({
    region                 = var.region
    artifactsBucket        = local.infra.artifacts_bucket_name
    rdsMasterSecretArn     = local.infra.rds_master_secret_arn
    rdsMlflowSecretName    = "mlflowops/rds/mlflow"
    rdsTasksSecretName     = "mlflowops/rds/tasks"
    terraformChartChecksum = local.platform_base_chart_checksum
  })]
  depends_on = [helm_release.external_secrets, helm_release.load_balancer_controller]
}
resource "helm_release" "argocd" {
  name             = "argo-cd"
  namespace        = "argocd"
  repository       = "https://argoproj.github.io/argo-helm"
  chart            = "argo-cd"
  version          = "10.17.0"
  create_namespace = true
  wait             = true
  atomic           = true
  values = [yamlencode({
    global        = { nodeSelector = { workload = "base" } }
    dex           = { enabled = false }
    notifications = { enabled = false }
    controller    = { resources = { requests = { cpu = "100m", memory = "256Mi" }, limits = { cpu = "500m", memory = "512Mi" } } }
    server = {
      service   = { type = "ClusterIP" }
      resources = { requests = { cpu = "50m", memory = "128Mi" }, limits = { cpu = "250m", memory = "256Mi" } }
    }
    repoServer = { resources = { requests = { cpu = "50m", memory = "128Mi" }, limits = { cpu = "250m", memory = "256Mi" } } }
    applicationSet = {
      enabled   = true
      resources = { requests = { cpu = "25m", memory = "64Mi" }, limits = { cpu = "150m", memory = "128Mi" } }
    }
    configs = { params = { "server.insecure" = true } }
  })]
  depends_on = [helm_release.platform_base]
}
resource "helm_release" "argocd_bootstrap" {
  name      = "argocd-bootstrap"
  namespace = "argocd"
  chart     = "${path.module}/../../deploy/charts/argocd-bootstrap"
  wait      = true
  atomic    = true
  values = [yamlencode({
    repoURL                = var.argocd_repo_url
    targetRevision         = var.argocd_target_revision
    mlflowImageRepository  = local.infra.ecr_repository_urls["mlflow"]
    mlflowRegion           = var.region
    mlflowArtifactsBucket  = local.infra.artifacts_bucket_name
    servingImageRepository = local.infra.ecr_repository_urls["serving"]
    certificateArn         = local.certificate_arn
    publicSubnetCidrs      = local.infra.public_subnet_cidrs
  })]
  depends_on = [helm_release.argocd]
}
