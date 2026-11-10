variable "name" { type = string }
variable "region" { type = string }
variable "vpc_cidr" {
  type        = string
  description = "The CIDR used by the fixed 10.20.x.x subnet layout. Change subnet definitions alongside this value."
  validation {
    condition     = var.vpc_cidr == "10.20.0.0/16"
    error_message = "The current foundation subnet plan is defined for 10.20.0.0/16. Update the subnet CIDRs in modules/network before changing this input."
  }
}
variable "tags" { type = map(string) }
