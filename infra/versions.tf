terraform {
  # 1.10+ for S3-native state locking (use_lockfile). DynamoDB locking is deprecated.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
  }

  # Remote state in S3. The settings live in backend/*.s3.tfbackend so the same code
  # works against real AWS or the local emulator:
  #   terraform init -backend-config=backend/emulator.s3.tfbackend
  # CI runs `terraform init -backend=false` and never touches state.
  backend "s3" {}
}
