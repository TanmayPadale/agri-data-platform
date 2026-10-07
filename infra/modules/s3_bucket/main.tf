# A private, versioned, encrypted bucket with a cool-down lifecycle rule.
# A module because every bucket in a project should get the same safe defaults,
# written once instead of copied.

resource "aws_s3_bucket" "this" {
  bucket = var.name
  # false on real AWS: Terraform refuses to delete a bucket that still holds data.
  # The emulator sets it to true so a teardown is one command.
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id
  versioning_configuration {
    status = "Enabled" # an overwritten or deleted object can be recovered
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    id     = "cool-down"
    status = "Enabled"
    filter {}

    # Infrequent Access is cheaper to store, dearer to read: right for old raw files.
    # Since late 2024, S3 skips objects under 128 KB for transitions by default, so
    # small daily JSON files stay in STANDARD (where they are already cheap).
    transition {
      days          = var.transition_to_ia_days
      storage_class = "STANDARD_IA"
    }

    noncurrent_version_expiration {
      noncurrent_days = 90 # old versions are kept for 90 days, then removed
    }
  }

  depends_on = [aws_s3_bucket_versioning.this]
}
