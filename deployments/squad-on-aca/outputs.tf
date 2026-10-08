output "resource_group_name" {
  description = "Resource group containing the optional Squad on ACA resources."
  value       = data.azurerm_resource_group.squad.name
}

output "container_app_environment_name" {
  description = "Dedicated Container Apps managed environment for the side track."
  value       = azurerm_container_app_environment.squad.name
}

output "agent_job_name" {
  description = "Container App Job that runs the Squad agent container."
  value       = azapi_resource.squad_agent_job.name
}

output "storage_account_name" {
  description = "Private Storage account that hosts the work queue."
  value       = azurerm_storage_account.squad.name
}

output "queue_name" {
  description = "Storage Queue used for Squad work items."
  value       = azapi_resource.work_queue.name
}

output "key_vault_name" {
  description = "Private Key Vault for the GitHub App private key and Copilot token."
  value       = azurerm_key_vault.squad.name
}

output "acr_login_server" {
  description = "ACR login server for the Squad agent image."
  value       = azurerm_container_registry.squad.login_server
}

output "squad_agent_client_id" {
  description = "Client ID for target-repo GitHub OIDC variables."
  value       = azurerm_user_assigned_identity.squad_agent.client_id
}
