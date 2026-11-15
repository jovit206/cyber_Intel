variable "atlas_org_id" {
  description = "MongoDB Atlas organization ID in which the training project is created."
  type        = string
}

variable "atlas_user_password" {
  description = "Strong password for the lab database user. Terraform state stores this value."
  type        = string
  sensitive   = true
  validation {
    condition     = length(var.atlas_user_password) >= 16
    error_message = "Use an Atlas database-user password with at least 16 characters."
  }
}

variable "atlas_client_cidr" {
  description = "Narrow IPv4 CIDR allowed to connect to the Atlas cluster (for example, your public IP followed by /32)."
  type        = string
  validation {
    condition     = can(cidrnetmask(var.atlas_client_cidr)) && var.atlas_client_cidr != "0.0.0.0/0"
    error_message = "Use a valid IPv4 CIDR for the client and do not allow every IPv4 address."
  }
}

variable "atlas_region" {
  description = "AWS region for the Atlas M0 cluster; confirm it is currently supported for M0."
  type        = string
  default     = "AP_SOUTH_1"
}
