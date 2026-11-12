terraform {
  required_version = ">= 1.11.0"
  required_providers {
    aws        = { source = "hashicorp/aws", version = "= 6.72.0" }
    helm       = { source = "hashicorp/helm", version = "= 3.3.0" }
    kubernetes = { source = "hashicorp/kubernetes", version = "= 3.4.0" }
    random     = { source = "hashicorp/random", version = "= 3.9.1" }
    tls        = { source = "hashicorp/tls", version = "= 4.4.2" }
  }
  backend "s3" {}
}
provider "aws" { region = var.region }
data "aws_eks_cluster" "this" { name = data.terraform_remote_state.infra.outputs.cluster_name }
data "aws_eks_cluster_auth" "this" { name = data.terraform_remote_state.infra.outputs.cluster_name }
provider "kubernetes" {
  host                   = data.aws_eks_cluster.this.endpoint
  cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)
  token                  = data.aws_eks_cluster_auth.this.token
}
provider "helm" {
  # Helm provider 3.x models the Kubernetes connection as one nested object.
  kubernetes = {
    host                   = data.aws_eks_cluster.this.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.this.certificate_authority[0].data)
    token                  = data.aws_eks_cluster_auth.this.token
  }
}
