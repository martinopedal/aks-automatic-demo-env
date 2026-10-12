data "azapi_resource" "upgrade_vm" {
  count       = var.upgrade == null ? 0 : 1
  type        = "Microsoft.Compute/virtualMachines@2024-11-01"
  resource_id = var.upgrade.vm_id
  response_export_values = [
    "properties.storageProfile.osDisk", "properties.hardwareProfile.vmSize"
  ]
}

data "azapi_resource" "upgrade_disk" {
  count                  = var.upgrade == null ? 0 : 1
  type                   = "Microsoft.Compute/disks@2024-03-02"
  resource_id            = var.upgrade.os_disk_id
  response_export_values = ["sku.name", "properties.diskSizeGB", "managedBy"]
}

# These are provider-managed actions on ONE existing VM, not new VM objects
# or shell provisioners. A failed update may leave it deallocated; recovery
# requires a fresh reviewed plan, never an automatic CLI start.
resource "azapi_resource_action" "upgrade_deallocate" {
  count       = var.upgrade == null ? 0 : 1
  type        = "Microsoft.Compute/virtualMachines@2024-11-01"
  resource_id = var.upgrade.vm_id
  action      = "deallocate"
  method      = "POST"
  when        = "apply"
}

resource "azapi_update_resource" "upgrade_disk" {
  count       = var.upgrade == null ? 0 : 1
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
  count       = var.upgrade == null ? 0 : 1
  type        = "Microsoft.Compute/virtualMachines@2024-11-01"
  resource_id = var.upgrade.vm_id
  action      = "start"
  method      = "POST"
  when        = "apply"
  depends_on  = [azapi_update_resource.upgrade_disk]
}

locals {
  machine_bootstrap_command = var.upgrade == null ? null : "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand ${textencodebase64(templatefile("${path.module}/scripts/Invoke-MachineBootstrap.ps1.tftpl", {
    script_url      = "https://raw.githubusercontent.com/${var.upgrade.bootstrap.repository}/${var.upgrade.bootstrap.commit}/${var.upgrade.bootstrap.script_path}"
    manifest_url    = "https://raw.githubusercontent.com/${var.upgrade.bootstrap.repository}/${var.upgrade.bootstrap.commit}/${var.upgrade.bootstrap.manifest_path}"
    script_sha256   = upper(var.upgrade.bootstrap.script_sha256)
    manifest_sha256 = upper(var.upgrade.bootstrap.manifest_sha256)
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
  }

  # CSE needs a running guest after the disk-only conversion.
  depends_on = [azapi_resource_action.upgrade_start]

  timeouts {
    create = "90m"
    update = "90m"
  }
}
