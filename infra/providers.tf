locals {
  # Emulator mode is switched on by setting var.aws_endpoint (see emulator.tfvars).
  emulator = var.aws_endpoint != null
}

provider "aws" {
  region = var.region

  # Only used with the local emulator. With aws_endpoint = null (real AWS) these are
  # all false, the endpoints block is not generated, and the provider behaves normally.
  s3_use_path_style           = local.emulator
  skip_credentials_validation = local.emulator
  skip_metadata_api_check     = local.emulator
  skip_requesting_account_id  = local.emulator

  dynamic "endpoints" {
    for_each = local.emulator ? [var.aws_endpoint] : []
    content {
      s3             = endpoints.value
      iam            = endpoints.value
      sts            = endpoints.value
      lambda         = endpoints.value
      cloudwatchlogs = endpoints.value
      events         = endpoints.value
    }
  }

  # Every resource gets these tags, so the cost explorer can group by project.
  default_tags {
    tags = {
      project    = "agri-data-platform"
      managed_by = "terraform"
    }
  }
}
