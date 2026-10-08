variable "resource_group_name" {
  description = "Pre-created resource group for the optional Squad on ACA side track."
  type        = string
  default     = "rg-squad-on-aca-demo"
}

variable "location" {
  description = "Azure region for all resources."
  type        = string
  default     = "swedencentral"
}

variable "resource_suffix" {
  description = "Lowercase alphanumeric suffix used for globally unique resource names. Set from a GitHub environment variable, not in source."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,8}$", var.resource_suffix))
    error_message = "resource_suffix must be 3-8 lowercase letters or numbers."
  }
}

variable "vnet_address_prefix" {
  description = "CIDR for the dedicated Squad on ACA virtual network. Keep the concrete value in GitHub environment variables, not in this public repo."
  type        = string
}

variable "aca_subnet_prefix" {
  description = "CIDR for the delegated Container Apps environment subnet. Keep the concrete value in GitHub environment variables."
  type        = string
}

variable "private_endpoint_subnet_prefix" {
  description = "CIDR for private endpoints. Keep the concrete value in GitHub environment variables."
  type        = string
}

variable "target_repositories" {
  description = "Repositories allowed to enqueue Squad work via GitHub OIDC, in owner/repo format."
  type        = list(string)
  default     = ["martinopedal/aks-automatic-demo-env"]

  validation {
    condition     = alltrue([for repo in var.target_repositories : can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo))])
    error_message = "Each target repository must be in owner/repo format."
  }
}

variable "github_app_id" {
  description = "GitHub App ID used by the agent container. Not secret; set via GitHub environment variable."
  type        = string
}

variable "github_app_installation_id" {
  description = "GitHub App installation ID used by the agent container. Not secret; set via GitHub environment variable."
  type        = string
}

variable "queue_name" {
  description = "Storage Queue name for Squad work items."
  type        = string
  default     = "squad-work-queue"
}

variable "agent_image" {
  description = "Container image for Haflidi's Squad agent. Defaults to this root's ACR; build/push the image before a live run."
  type        = string
  default     = null
}

variable "agent_job_config" {
  description = "Container App Job sizing and concurrency."
  type = object({
    cpu             = optional(number, 1.0)
    memory          = optional(string, "2Gi")
    max_executions  = optional(number, 3)
    timeout_seconds = optional(number, 1800)
  })
  default = {}
}

variable "manage_runtime_role_assignments" {
  description = "Set true only after an admin confirms the pipeline can create the required data-plane role assignments in this resource group."
  type        = bool
  default     = false
}
