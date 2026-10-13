data "azapi_resource" "upgrade_vm" {
  count       = local.vm_preservation_enabled ? 1 : 0
  type        = "Microsoft.Compute/virtualMachines@2024-11-01"
  resource_id = local.bound_vm_id
  response_export_values = [
    "properties.storageProfile.osDisk", "properties.hardwareProfile.vmSize"
  ]
}

data "azapi_resource" "upgrade_disk" {
  count                  = local.vm_preservation_enabled ? 1 : 0
  type                   = "Microsoft.Compute/disks@2024-03-02"
  resource_id            = local.bound_os_disk_id
  response_export_values = ["sku.name", "properties.diskSizeGB", "managedBy"]
}

# These are provider-managed actions on ONE existing VM, not new VM objects
# or shell provisioners. A failed update may leave it deallocated; recovery
# requires a fresh reviewed plan, never an automatic CLI start.
locals {
  legacy_creation_enabled     = var.legacy_clean_machine && var.upgrade == null
  vm_preservation_enabled     = !local.legacy_creation_enabled
  existing_vm_id              = "${local.rg_id}/providers/Microsoft.Compute/virtualMachines/vm-${local.name}"
  bound_vm_id                 = var.upgrade == null ? local.existing_vm_id : var.upgrade.vm_id
  bound_os_disk_id            = local.vm_preservation_enabled ? (var.upgrade == null ? data.azapi_resource.upgrade_vm[0].output.properties.storageProfile.osDisk.managedDisk.id : var.upgrade.os_disk_id) : null
  observed_vm_size            = var.upgrade == null ? "Standard_D8s_v5" : var.upgrade.baseline.vm_size
  observed_disk_sku           = var.upgrade == null ? "Premium_LRS" : var.upgrade.baseline.os_disk_sku
  upgrade_performance_pending = var.upgrade == null ? false : var.upgrade.baseline.vm_size == "Standard_D4s_v5"
}

resource "azapi_resource_action" "upgrade_deallocate" {
  count       = local.upgrade_performance_pending ? 1 : 0
  type        = "Microsoft.Compute/virtualMachines@2024-11-01"
  resource_id = var.upgrade.vm_id
  action      = "deallocate"
  method      = "POST"
  when        = "apply"
}

resource "azapi_update_resource" "upgrade_disk" {
  count       = local.upgrade_performance_pending ? 1 : 0
  type        = "Microsoft.Compute/disks@2024-03-02"
  resource_id = var.upgrade.os_disk_id
  body        = { sku = { name = var.upgrade.os_disk_sku } }

  lifecycle {
    prevent_destroy = true
  }

  # Disk conversion requires deallocation; the VM resize finishes first.
  depends_on = [azapi_resource.vm]
}

resource "azapi_resource_action" "upgrade_start" {
  count       = local.upgrade_performance_pending ? 1 : 0
  type        = "Microsoft.Compute/virtualMachines@2024-11-01"
  resource_id = var.upgrade.vm_id
  action      = "start"
  method      = "POST"
  when        = "apply"
  depends_on  = [azapi_update_resource.upgrade_disk]
}

locals {
  machine_bootstrap_wrapper = var.upgrade == null ? null : templatefile("${path.module}/scripts/Invoke-MachineBootstrap.ps1.tftpl", {
    script_url      = "https://raw.githubusercontent.com/${var.upgrade.bootstrap.repository}/${var.upgrade.bootstrap.commit}/${var.upgrade.bootstrap.script_path}"
    manifest_url    = "https://raw.githubusercontent.com/${var.upgrade.bootstrap.repository}/${var.upgrade.bootstrap.commit}/${var.upgrade.bootstrap.manifest_path}"
    script_sha256   = upper(var.upgrade.bootstrap.script_sha256)
    manifest_sha256 = upper(var.upgrade.bootstrap.manifest_sha256)
  })
  # Compress only the reviewed wrapper in memory. No payload/credentials file,
  # checksum bypass or command-shell quoting; PS5.1 decodes the UTF-8 GZip.
  machine_bootstrap_command = var.upgrade == null ? null : "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand ${textencodebase64(templatefile("${path.module}/scripts/Invoke-CompressedBootstrap.ps1.tftpl", {
    payload_gzip = base64gzip(local.machine_bootstrap_wrapper)
  }), "UTF-16LE")}"
}

resource "azapi_resource" "machine_bootstrap" {
  count     = var.upgrade == null ? 0 : 1
  type      = "Microsoft.Compute/virtualMachines/extensions@2024-11-01"
  name      = "DemoMachineBootstrap"
  location  = local.location
  parent_id = azapi_resource.vm.id
  tags      = local.tags
  body = {
    properties = {
      publisher               = "Microsoft.Compute"
      type                    = "CustomScriptExtension"
      typeHandlerVersion      = "1.10"
      autoUpgradeMinorVersion = false
      settings = {
        commandToExecute = local.machine_bootstrap_command
      }
    }
  }

  lifecycle {
    prevent_destroy = true

    precondition {
      condition     = length(local.machine_bootstrap_command) <= 7500
      error_message = "CSE command exceeds the 7500-character safe launch bound (Windows cmd limit 8191, including prefix margin). Shorten the immutable payload paths; never bypass hashes."
    }
  }

  # CSE needs a running guest after the disk-only conversion.
  depends_on = [azapi_resource_action.upgrade_start]

  timeouts {
    create = "90m"
    update = "90m"
  }
}
