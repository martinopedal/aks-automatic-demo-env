# All runs are plan-only. BOTH configured providers are mocked; no modules,
# external programs, provisioners, credential access or live ARM effects.
mock_provider "azapi" {
  mock_resource "azapi_resource" {
    defaults = {
      output = { properties = { ipAddress = "203.0.113.1", vmId = "offline-vm" } }
    }
  }
}
mock_provider "azurerm" {}

override_data {
  target = data.azapi_client_config.current
  values = { subscription_id = "00000000-0000-0000-0000-000000000001" }
}

override_data {
  target = data.azapi_resource.upgrade_vm[0]
  values = {
    output = {
      properties = {
        hardwareProfile = { vmSize = "Standard_D4s_v5" }
        storageProfile = {
          osDisk = {
            name         = "restored-offline-disk"
            createOption = "FromImage"
            managedDisk  = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-demo-vm-online/providers/Microsoft.Compute/disks/restored-offline-disk" }
          }
        }
      }
    }
  }
}

override_data {
  target = data.azapi_resource.upgrade_disk[0]
  values = {
    output = {
      sku        = { name = "Standard_LRS" }
      managedBy  = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-demo-vm-online/providers/Microsoft.Compute/virtualMachines/vm-demo-copilot"
      properties = { diskSizeGB = 127 }
    }
  }
}

variables {
  upgrade = {
    vm_id      = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-demo-vm-online/providers/Microsoft.Compute/virtualMachines/vm-demo-copilot"
    os_disk_id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-demo-vm-online/providers/Microsoft.Compute/disks/restored-offline-disk"
    bootstrap = {
      repository      = "example/offline-payload"
      commit          = "f04ec69afe46971118493b8a1434a1e2b398f814"
      script_path     = "public/scripts/bootstrap-demo-vm.ps1"
      manifest_path   = "public/scripts/bootstrap-demo-vm.packages.json"
      script_sha256   = "3cc9fe4eb576c3795d0550e235c97d94227a992b32bd078ce6b2920fdc2c2ca8"
      manifest_sha256 = "9ea0fd7ab57759ee029b5604e17fd579c3e84f4f5c63e664c81837fb501cc7c0"
      system_accepted = true
    }
  }
}

run "retains_existing_disk_and_addresses" {
  command = plan
  override_resource {
    target          = azapi_resource.nic
    override_during = plan
    values = {
      id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-demo-vm-online/providers/Microsoft.Network/networkInterfaces/nic-demo-copilot"
    }
  }
  assert {
    condition     = azapi_resource.vm.body.properties.hardwareProfile.vmSize == "Standard_D8s_v5"
    error_message = "Existing azapi_resource.vm must receive exactly D8s_v5."
  }
  assert {
    condition = (
      azapi_resource.vm.body.properties.storageProfile.osDisk.managedDisk.id == var.upgrade.os_disk_id &&
      azapi_resource.vm.body.properties.storageProfile.osDisk.name == "restored-offline-disk" &&
      azapi_resource.vm.body.properties.storageProfile.osDisk.createOption == "FromImage" &&
      azapi_resource.vm.body.properties.storageProfile.osDisk.deleteOption == "Detach" &&
      azapi_resource.vm.body.properties.storageProfile.osDisk.managedDisk.storageAccountType == "Standard_LRS"
    )
    error_message = "VM must retain current restored disk creation semantics and SKU before the separate PATCH."
  }
  assert {
    condition = (
      azapi_update_resource.upgrade_disk[0].resource_id == var.upgrade.os_disk_id &&
      azapi_update_resource.upgrade_disk[0].body.sku.name == "Premium_LRS" &&
      !can(azapi_update_resource.upgrade_disk[0].body.properties)
    )
    error_message = "Exact disk PATCH must contain SKU only; capacity is not managed."
  }
  assert {
    condition = (
      azapi_resource_action.upgrade_deallocate[0].action == "deallocate" &&
      azapi_resource_action.upgrade_start[0].action == "start" &&
      azapi_resource_action.upgrade_deallocate[0].resource_id == var.upgrade.vm_id &&
      azapi_resource_action.upgrade_start[0].resource_id == var.upgrade.vm_id
    )
    error_message = "Provider-managed operations must act on the same VM."
  }
  assert {
    condition = (
      azapi_resource.machine_bootstrap[0].name == "DemoMachineBootstrap" &&
      azapi_resource.machine_bootstrap[0].body.properties.publisher == "Microsoft.Compute" &&
      azapi_resource.machine_bootstrap[0].body.properties.type == "CustomScriptExtension" &&
      startswith(azapi_resource.machine_bootstrap[0].body.properties.settings.commandToExecute, "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand ")
      && length(azapi_resource.machine_bootstrap[0].body.properties.settings.commandToExecute) <= 7500
    )
    error_message = "One real machine CSE command must fit the Windows cmd launch bound with margin, not a status-only bootstrap promise."
  }
}

run "legacy_clean_machine_defaults" {
  command = plan
  variables {
    upgrade        = null
    admin_password = "OfflineTestOnly!123456"
  }
  assert {
    condition = (
      azapi_resource.vm.body.properties.hardwareProfile.vmSize == "Standard_D4s_v5" &&
      azapi_resource.vm.body.properties.storageProfile.osDisk.createOption == "FromImage" &&
      azapi_resource.vm.body.properties.storageProfile.osDisk.managedDisk.storageAccountType == "StandardSSD_LRS" &&
      length(azapi_resource.machine_bootstrap) == 0 &&
      length(azapi_update_resource.upgrade_disk) == 0 &&
      length(azapi_resource_action.upgrade_start) == 0
    )
    error_message = "Null upgrade must preserve the legacy clean-machine creation contract."
  }
}

run "reject_wrong_sku" {
  command = plan
  variables {
    upgrade = merge(var.upgrade, { vm_size = "Standard_D16s_v5" })
  }
  expect_failures = [var.upgrade]
}

run "reject_wrong_disk" {
  command = plan
  variables {
    upgrade = merge(var.upgrade, { os_disk_id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-demo-vm-online/providers/Microsoft.Compute/disks/different-offline-disk" })
  }
  expect_failures = [azapi_resource.vm]
}

run "reject_disk_capacity_drift" {
  command = plan
  override_data {
    target = data.azapi_resource.upgrade_disk[0]
    values = {
      output = {
        sku        = { name = "Standard_LRS" }
        managedBy  = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-demo-vm-online/providers/Microsoft.Compute/virtualMachines/vm-demo-copilot"
        properties = { diskSizeGB = 256 }
      }
    }
  }
  expect_failures = [azapi_resource.vm]
}

run "reject_unaccepted_system_payload" {
  command = plan
  variables {
    upgrade = merge(var.upgrade, { bootstrap = merge(var.upgrade.bootstrap, { system_accepted = false }) })
  }
  expect_failures = [var.upgrade]
}

run "reject_mutable_payload" {
  command = plan
  variables {
    upgrade = merge(var.upgrade, { bootstrap = merge(var.upgrade.bootstrap, { commit = "main" }) })
  }
  expect_failures = [var.upgrade]
}

# Synthetic current D8/Premium readback; providers remain mocked and plan-only.
run "bootstrap_only_retained_d8_premium" {
  command = plan

  variables {
    upgrade = merge(var.upgrade, { baseline = { vm_size = "Standard_D8s_v5", os_disk_sku = "Premium_LRS" } })
  }
  override_data {
    target = data.azapi_resource.upgrade_vm[0]
    values = {
      output = {
        properties = {
          hardwareProfile = { vmSize = "Standard_D8s_v5" }
          storageProfile = {
            osDisk = {
              name         = "restored-offline-disk"
              createOption = "FromImage"
              caching      = "ReadWrite"
              deleteOption = "Detach"
              managedDisk  = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-demo-vm-online/providers/Microsoft.Compute/disks/restored-offline-disk", storageAccountType = "Premium_LRS" }
            }
          }
        }
      }
    }
  }
  override_data {
    target = data.azapi_resource.upgrade_disk[0]
    values = {
      output = {
        sku        = { name = "Premium_LRS" }
        managedBy  = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-demo-vm-online/providers/Microsoft.Compute/virtualMachines/vm-demo-copilot"
        properties = { diskSizeGB = 127 }
      }
    }
  }

  override_resource {
    target          = azapi_resource.nic
    override_during = plan
    values          = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-demo-vm-online/providers/Microsoft.Network/networkInterfaces/nic-demo-copilot" }
  }
  assert {
    condition = (
      azapi_resource.vm.body.properties.hardwareProfile.vmSize == "Standard_D8s_v5" &&
      azapi_resource.vm.body.properties.storageProfile.osDisk.managedDisk.id == var.upgrade.os_disk_id &&
      azapi_resource.vm.body.properties.storageProfile.osDisk.managedDisk.storageAccountType == "Premium_LRS" &&
      azapi_resource.vm.body.properties.storageProfile.osDisk.deleteOption == "Detach" &&
      length(azapi_resource_action.upgrade_deallocate) == 0 &&
      length(azapi_resource_action.upgrade_start) == 0 &&
      length(azapi_update_resource.upgrade_disk) == 0 &&
      length(azapi_resource.machine_bootstrap) == 1
    )
    error_message = "Continuation must retain the exact Premium disk and VM without performance/power/disk operations; only bootstrap remains."
  }
}

run "reject_continuation_indirect_disk_delete" {
  command = plan

  variables {
    upgrade = merge(var.upgrade, { baseline = { vm_size = "Standard_D8s_v5", os_disk_sku = "Premium_LRS" } })
  }
  override_data {
    target = data.azapi_resource.upgrade_vm[0]
    values = {
      output = {
        properties = {
          hardwareProfile = { vmSize = "Standard_D8s_v5" }
          storageProfile = {
            osDisk = {
              name         = "restored-offline-disk"
              createOption = "FromImage"
              caching      = "ReadWrite"
              deleteOption = "Delete"
              managedDisk  = { id = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-demo-vm-online/providers/Microsoft.Compute/disks/restored-offline-disk", storageAccountType = "Premium_LRS" }
            }
          }
        }
      }
    }
  }
  override_data {
    target = data.azapi_resource.upgrade_disk[0]
    values = {
      output = {
        sku        = { name = "Premium_LRS" }
        managedBy  = "/subscriptions/00000000-0000-0000-0000-000000000001/resourceGroups/rg-demo-vm-online/providers/Microsoft.Compute/virtualMachines/vm-demo-copilot"
        properties = { diskSizeGB = 127 }
      }
    }
  }

  expect_failures = [azapi_resource.vm]
}

run "reject_unsupported_mixed_baseline" {
  command = plan
  variables {
    upgrade = merge(var.upgrade, { baseline = { vm_size = "Standard_D8s_v5", os_disk_sku = "Standard_LRS" } })
  }
  expect_failures = [var.upgrade]
}
