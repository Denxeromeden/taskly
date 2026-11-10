variable "project" {
  type        = string
  description = "Short project identifier used in resource names."
  default     = "mlflowops"
}

variable "environment" {
  type        = string
  description = "Environment name."
  default     = "demo"
}

variable "region" {
  type        = string
  description = "AWS region."
  default     = "eu-central-1"
}

variable "owner" {
  type        = string
  description = "Owner tag."
  default     = "taskly"
}
