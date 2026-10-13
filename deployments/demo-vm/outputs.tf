output "vm_id" {
  description = "Existing demo VM resource ID; the upgrade lane preserves this identity."
  value       = azapi_resource.vm.id
}

output "bastion_name" {
  description = "Unchanged Standard Bastion host used for private native RDP."
  value       = azapi_resource.bastion.name
}

output "connect_command" {
  description = "Native-client RDP through Bastion with Microsoft Entra ID (tenant members)."
  value       = "az network bastion rdp --name ${azapi_resource.bastion.name} --resource-group rg-demo-vm-online --target-resource-id ${azapi_resource.vm.id} --enable-mfa"
}

output "egress_ip" {
  description = "Existing NAT Gateway public egress address, not a public VM address."
  value       = azapi_resource.pip_nat.output.properties.ipAddress
}
