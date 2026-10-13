variable "admin_password" {
  description = "Local administrator password, generated per run by the workflow. Ephemeral: never written to the plan file or state. Sent only on VM creation."
  type        = string
  sensitive   = true
  ephemeral   = true
  default     = null
}

variable "entra_admin_object_ids" {
  description = "Object IDs of tenant MEMBER users who sign in to the VM with Microsoft Entra ID (Virtual Machine Administrator Login). B2B guests cannot use Entra sign-in to VMs. Set from the GitHub environment, never committed."
  type        = list(string)
  default     = []
  nullable    = false
}

variable "bastion_user_object_ids" {
  description = "Object IDs of users (members or guests) who may connect through Bastion (Reader on the resource group). Set from the GitHub environment, never committed."
  type        = list(string)
  default     = []
  nullable    = false
}

variable "auto_shutdown_time" {
  description = "Daily auto-shutdown time (HHmm, W. Europe Standard Time) to cap cost."
  type        = string
  default     = "1900"

  validation {
    condition     = can(regex("^([01][0-9]|2[0-3])[0-5][0-9]$", var.auto_shutdown_time))
    error_message = "auto_shutdown_time must be HHmm, for example 1900."
  }

}

variable "upgrade" {
  description = "Null preserves the legacy clean-machine lane. Non-null is a single reviewed in-place upgrade of the existing VM and exact attached 127-GiB disk; private environment binding, never an import or replacement."
  type = object({
    vm_id       = string
    os_disk_id  = string
    vm_size     = optional(string, "Standard_D8s_v5")
    os_disk_sku = optional(string, "Premium_LRS")
    bootstrap = object({
      repository      = string
      commit          = string
      script_path     = string
      manifest_path   = string
      script_sha256   = string
      manifest_sha256 = string
      system_accepted = bool
    })
  })
  default = null

  validation {
    condition = var.upgrade == null ? true : (
      var.upgrade.vm_size == "Standard_D8s_v5" &&
      var.upgrade.os_disk_sku == "Premium_LRS" &&
      can(regex("^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Compute/virtualMachines/vm-demo-copilot$", var.upgrade.vm_id)) &&
      can(regex("^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Compute/disks/[^/]+$", var.upgrade.os_disk_id))
    )
    error_message = "Upgrade supports only the existing vm-demo-copilot, exact managed OS disk, D8s_v5 and Premium_LRS; no replacement or capacity change."
  }

  validation {
    condition = var.upgrade == null ? true : (
      can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.upgrade.bootstrap.repository)) &&
      can(regex("^[a-f0-9]{40}$", var.upgrade.bootstrap.commit)) &&
      endswith(var.upgrade.bootstrap.script_path, "/bootstrap-demo-vm.ps1") &&
      endswith(var.upgrade.bootstrap.manifest_path, "/bootstrap-demo-vm.packages.json") &&
      dirname(var.upgrade.bootstrap.script_path) == dirname(var.upgrade.bootstrap.manifest_path) &&
      can(regex("^[A-Za-z0-9_./-]+$", var.upgrade.bootstrap.script_path)) &&
      can(regex("^[A-Za-z0-9_./-]+$", var.upgrade.bootstrap.manifest_path)) &&
      !strcontains(var.upgrade.bootstrap.script_path, "..") &&
      !strcontains(var.upgrade.bootstrap.manifest_path, "..") &&
      can(regex("^[a-fA-F0-9]{64}$", var.upgrade.bootstrap.script_sha256)) &&
      can(regex("^[a-fA-F0-9]{64}$", var.upgrade.bootstrap.manifest_sha256)) &&
      var.upgrade.bootstrap.system_accepted
    )
    error_message = "Bootstrap needs an accepted SYSTEM packages-only adapter and sibling manifest pinned to a full published commit and two SHA256 digests; no mutable URLs."
  }
}
