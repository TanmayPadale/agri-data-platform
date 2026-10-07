variable "region" {
  description = "AWS region for every resource."
  type        = string
  default     = "ap-southeast-2"
}

variable "suffix" {
  description = "Makes globally unique S3 bucket names, e.g. your initials plus a number."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]{3,20}$", var.suffix))
    error_message = "suffix must be 3 to 20 lowercase letters, digits or hyphens."
  }
}

variable "aws_endpoint" {
  description = "Set to an emulator URL (http://localhost:4566) to use LocalStack instead of AWS."
  type        = string
  default     = null
}

variable "schedule_expression" {
  description = "When the weather Lambda runs. 20:30 UTC is 06:30 or 07:30 in Sydney."
  type        = string
  default     = "cron(30 20 * * ? *)"
}

variable "log_retention_days" {
  description = "How long the Lambda's logs are kept. Without this, CloudWatch keeps them forever."
  type        = number
  default     = 14
}

variable "force_destroy" {
  description = "Let destroy remove non-empty buckets. Keep false on real AWS."
  type        = bool
  default     = false
}
