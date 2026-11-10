terraform {
  required_version = ">= 1.11.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.72.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = var.project
      Env       = var.environment
      Owner     = var.owner
      ManagedBy = "Terraform"
      Layer     = "bootstrap"
    }
  }
}
