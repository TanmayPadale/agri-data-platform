variable "name" {
  description = "Globally unique bucket name."
  type        = string
}

variable "transition_to_ia_days" {
  description = "Days before objects move to STANDARD_IA (30 is the minimum S3 allows)."
  type        = number
  default     = 30

  validation {
    condition     = var.transition_to_ia_days >= 30
    error_message = "S3 requires at least 30 days before a transition to STANDARD_IA."
  }
}

variable "force_destroy" {
  description = "Allow terraform destroy to delete a bucket that still has objects."
  type        = bool
  default     = false
}
