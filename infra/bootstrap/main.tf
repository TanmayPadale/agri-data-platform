# Bootstrap: the bucket that holds Terraform's own state.
#
# Chicken and egg: the main config keeps its state in S3, so that bucket must exist
# before `terraform init` can use it. This tiny config creates it, and keeps its own
# (one-resource) state in a local file. Run once per account (or per emulator):
#   terraform -chdir=infra/bootstrap init
#   terraform -chdir=infra/bootstrap apply -var suffix=<yours>

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

variable "suffix" {
  type = string
}

variable "region" {
  type    = string
  default = "ap-southeast-2"
}

variable "aws_endpoint" {
  type    = string
  default = null
}

variable "force_destroy" {
  type    = bool
  default = false
}

provider "aws" {
  region                      = var.region
  s3_use_path_style           = var.aws_endpoint != null
  skip_credentials_validation = var.aws_endpoint != null
  skip_metadata_api_check     = var.aws_endpoint != null
  skip_requesting_account_id  = var.aws_endpoint != null

  dynamic "endpoints" {
    for_each = var.aws_endpoint != null ? [var.aws_endpoint] : []
    content {
      s3  = endpoints.value
      sts = endpoints.value
    }
  }
}

module "state_bucket" {
  source        = "../modules/s3_bucket"
  name          = "agri-tf-state-${var.suffix}"
  force_destroy = var.force_destroy
}

output "state_bucket" {
  value = module.state_bucket.name
}
