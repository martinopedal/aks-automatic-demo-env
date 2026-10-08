data "azapi_client_config" "current" {}

data "azurerm_resource_group" "squad" {
  name = var.resource_group_name
}

locals {
  rg_id                = "/subscriptions/${data.azapi_client_config.current.subscription_id}/resourceGroups/${var.resource_group_name}"
  compact_suffix       = lower(var.resource_suffix)
  storage_account_name = "stsquadaca${local.compact_suffix}"
  acr_name             = "crsquadaca${local.compact_suffix}"
  key_vault_name       = "kv-squad-aca-${local.compact_suffix}"
  agent_image          = coalesce(var.agent_image, "${azurerm_container_registry.squad.login_server}/squad-agent:latest")

  tags = {
    owner     = "martinopedal"
    expiry    = "2026-10-31"
    purpose   = "nic-2026-optional-squad-on-aca"
    lifecycle = "demo"
    purgeable = "true"
  }
}

resource "azapi_resource" "nsg_aca" {
  type      = "Microsoft.Network/networkSecurityGroups@2024-05-01"
  name      = "nsg-squad-on-aca-env"
  location  = var.location
  parent_id = local.rg_id
  tags      = local.tags

  body = {
    properties = {
      securityRules = []
    }
  }
}

resource "azapi_resource" "nsg_private_endpoints" {
  type      = "Microsoft.Network/networkSecurityGroups@2024-05-01"
  name      = "nsg-squad-on-aca-private-endpoints"
  location  = var.location
  parent_id = local.rg_id
  tags      = local.tags

  body = {
    properties = {
      securityRules = []
    }
  }
}

resource "azapi_resource" "vnet" {
  type      = "Microsoft.Network/virtualNetworks@2024-05-01"
  name      = "vnet-squad-on-aca-demo"
  location  = var.location
  parent_id = local.rg_id
  tags      = local.tags

  body = {
    properties = {
      addressSpace = { addressPrefixes = [var.vnet_address_prefix] }
    }
  }
}

resource "azapi_resource" "snet_aca" {
  type      = "Microsoft.Network/virtualNetworks/subnets@2024-05-01"
  name      = "snet-squad-on-aca-env"
  parent_id = azapi_resource.vnet.id

  body = {
    properties = {
      addressPrefix        = var.aca_subnet_prefix
      networkSecurityGroup = { id = azapi_resource.nsg_aca.id }
      delegations = [
        {
          name       = "container-apps-env"
          properties = { serviceName = "Microsoft.App/environments" }
        }
      ]
    }
  }
}

resource "azapi_resource" "snet_private_endpoints" {
  type      = "Microsoft.Network/virtualNetworks/subnets@2024-05-01"
  name      = "snet-squad-on-aca-private-endpoints"
  parent_id = azapi_resource.vnet.id

  body = {
    properties = {
      addressPrefix                     = var.private_endpoint_subnet_prefix
      networkSecurityGroup              = { id = azapi_resource.nsg_private_endpoints.id }
      privateEndpointNetworkPolicies    = "Disabled"
      privateLinkServiceNetworkPolicies = "Enabled"
    }
  }

  depends_on = [azapi_resource.snet_aca]
}

resource "azurerm_log_analytics_workspace" "squad" {
  name                = "law-squad-on-aca-demo"
  location            = data.azurerm_resource_group.squad.location
  resource_group_name = data.azurerm_resource_group.squad.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
  tags                = local.tags
}

resource "azurerm_storage_account" "squad" {
  #checkov:skip=CKV2_AZURE_1:Short-lived demo queue uses Microsoft-managed keys; no customer data is stored.
  #checkov:skip=CKV_AZURE_206:Zone-redundant storage is enough for a single-region optional demo.
  #checkov:skip=CKV_AZURE_33:Queue logging is configured through azurerm_storage_account_queue_properties; Checkov does not correlate it.
  name                            = local.storage_account_name
  location                        = data.azurerm_resource_group.squad.location
  resource_group_name             = data.azurerm_resource_group.squad.name
  account_tier                    = "Standard"
  account_replication_type        = "ZRS"
  account_kind                    = "StorageV2"
  min_tls_version                 = "TLS1_2"
  public_network_access_enabled   = false
  shared_access_key_enabled       = false
  default_to_oauth_authentication = true
  allow_nested_items_to_be_public = false
  tags                            = local.tags

  network_rules {
    default_action = "Deny"
    bypass         = ["AzureServices"]
  }

  blob_properties {
    delete_retention_policy {
      days = 7
    }
    container_delete_retention_policy {
      days = 7
    }
  }
}

resource "azurerm_storage_account_queue_properties" "squad" {
  storage_account_id = azurerm_storage_account.squad.id

  logging {
    delete                = true
    read                  = true
    write                 = true
    version               = "1.0"
    retention_policy_days = 7
  }
}

resource "azapi_resource" "queue_service" {
  type      = "Microsoft.Storage/storageAccounts/queueServices@2023-05-01"
  name      = "default"
  parent_id = azurerm_storage_account.squad.id

  body = {
    properties = {}
  }
}

resource "azapi_resource" "work_queue" {
  type      = "Microsoft.Storage/storageAccounts/queueServices/queues@2023-05-01"
  name      = var.queue_name
  parent_id = azapi_resource.queue_service.id

  body = {
    properties = {
      metadata = {}
    }
  }
}

resource "azurerm_private_dns_zone" "queue" {
  name                = "privatelink.queue.core.windows.net"
  resource_group_name = data.azurerm_resource_group.squad.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "queue" {
  name                  = "pdns-queue-squad-on-aca-demo"
  resource_group_name   = data.azurerm_resource_group.squad.name
  private_dns_zone_name = azurerm_private_dns_zone.queue.name
  virtual_network_id    = azapi_resource.vnet.id
  registration_enabled  = false
  tags                  = local.tags
}

resource "azurerm_private_endpoint" "queue" {
  name                = "pe-squad-on-aca-queue"
  location            = data.azurerm_resource_group.squad.location
  resource_group_name = data.azurerm_resource_group.squad.name
  subnet_id           = azapi_resource.snet_private_endpoints.id
  tags                = local.tags

  private_service_connection {
    name                           = "psc-squad-on-aca-queue"
    private_connection_resource_id = azurerm_storage_account.squad.id
    subresource_names              = ["queue"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "queue"
    private_dns_zone_ids = [azurerm_private_dns_zone.queue.id]
  }
}

resource "azurerm_key_vault" "squad" {
  #checkov:skip=CKV_AZURE_110:Optional demo vault must remain purgeable for post-session teardown.
  #checkov:skip=CKV_AZURE_42:Soft delete is platform-enabled; purge protection is intentionally off for this demo.
  name                          = local.key_vault_name
  location                      = data.azurerm_resource_group.squad.location
  resource_group_name           = data.azurerm_resource_group.squad.name
  tenant_id                     = data.azapi_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = false
  soft_delete_retention_days    = 7
  public_network_access_enabled = false
  tags                          = local.tags

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
  }
}

resource "azurerm_private_dns_zone" "vault" {
  name                = "privatelink.vaultcore.azure.net"
  resource_group_name = data.azurerm_resource_group.squad.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "vault" {
  name                  = "pdns-vault-squad-on-aca-demo"
  resource_group_name   = data.azurerm_resource_group.squad.name
  private_dns_zone_name = azurerm_private_dns_zone.vault.name
  virtual_network_id    = azapi_resource.vnet.id
  registration_enabled  = false
  tags                  = local.tags
}

resource "azurerm_private_endpoint" "vault" {
  name                = "pe-squad-on-aca-vault"
  location            = data.azurerm_resource_group.squad.location
  resource_group_name = data.azurerm_resource_group.squad.name
  subnet_id           = azapi_resource.snet_private_endpoints.id
  tags                = local.tags

  private_service_connection {
    name                           = "psc-squad-on-aca-vault"
    private_connection_resource_id = azurerm_key_vault.squad.id
    subresource_names              = ["vault"]
    is_manual_connection           = false
  }

  private_dns_zone_group {
    name                 = "vault"
    private_dns_zone_ids = [azurerm_private_dns_zone.vault.id]
  }
}

resource "azurerm_container_registry" "squad" {
  #checkov:skip=CKV_AZURE_139:Optional demo uses Basic ACR with AcrPull; private endpoints require Premium cost.
  #checkov:skip=CKV_AZURE_163:Defender image scanning is not enabled for the short-lived optional demo.
  #checkov:skip=CKV_AZURE_164:Trusted signing is outside the scope of this optional side track.
  #checkov:skip=CKV_AZURE_165:Single-region demo intentionally avoids geo-replication cost.
  #checkov:skip=CKV_AZURE_166:Quarantine/verified image flow is outside the side-track scope.
  #checkov:skip=CKV_AZURE_167:Untagged manifest retention is a Premium ACR feature; Basic keeps cost low.
  #checkov:skip=CKV_AZURE_233:Zone-redundant ACR requires Premium; not justified for this optional demo.
  #checkov:skip=CKV_AZURE_237:Dedicated data endpoints require Premium; not justified for this optional demo.
  name                          = local.acr_name
  location                      = data.azurerm_resource_group.squad.location
  resource_group_name           = data.azurerm_resource_group.squad.name
  sku                           = "Basic"
  admin_enabled                 = false
  public_network_access_enabled = true
  tags                          = local.tags
}

resource "azurerm_container_app_environment" "squad" {
  name                           = "cae-squad-on-aca-demo"
  location                       = data.azurerm_resource_group.squad.location
  resource_group_name            = data.azurerm_resource_group.squad.name
  log_analytics_workspace_id     = azurerm_log_analytics_workspace.squad.id
  infrastructure_subnet_id       = azapi_resource.snet_aca.id
  internal_load_balancer_enabled = true
  tags                           = local.tags
}

resource "azurerm_user_assigned_identity" "squad_agent" {
  name                = "id-squad-on-aca-agent"
  location            = data.azurerm_resource_group.squad.location
  resource_group_name = data.azurerm_resource_group.squad.name
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "target_repo_main" {
  for_each            = toset(var.target_repositories)
  name                = "gh-${replace(replace(each.value, "/", "-"), ".", "-")}-main"
  resource_group_name = data.azurerm_resource_group.squad.name
  parent_id           = azurerm_user_assigned_identity.squad_agent.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = "https://token.actions.githubusercontent.com"
  subject             = "repo:${each.value}:ref:refs/heads/main"
}

resource "azurerm_role_assignment" "queue_reader" {
  count                = var.manage_runtime_role_assignments ? 1 : 0
  scope                = azurerm_storage_account.squad.id
  role_definition_name = "Storage Queue Data Reader"
  principal_id         = azurerm_user_assigned_identity.squad_agent.principal_id
}

resource "azurerm_role_assignment" "queue_processor" {
  count                = var.manage_runtime_role_assignments ? 1 : 0
  scope                = azurerm_storage_account.squad.id
  role_definition_name = "Storage Queue Data Message Processor"
  principal_id         = azurerm_user_assigned_identity.squad_agent.principal_id
}

resource "azurerm_role_assignment" "queue_sender" {
  count                = var.manage_runtime_role_assignments ? 1 : 0
  scope                = azurerm_storage_account.squad.id
  role_definition_name = "Storage Queue Data Message Sender"
  principal_id         = azurerm_user_assigned_identity.squad_agent.principal_id
}

resource "azurerm_role_assignment" "key_vault_secrets_user" {
  count                = var.manage_runtime_role_assignments ? 1 : 0
  scope                = azurerm_key_vault.squad.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.squad_agent.principal_id
}

resource "azurerm_role_assignment" "acr_pull" {
  count                = var.manage_runtime_role_assignments ? 1 : 0
  scope                = azurerm_container_registry.squad.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.squad_agent.principal_id
}

resource "azapi_resource" "squad_agent_job" {
  type      = "Microsoft.App/jobs@2025-01-01"
  name      = "job-squad-on-aca-agent"
  location  = data.azurerm_resource_group.squad.location
  parent_id = local.rg_id
  tags      = local.tags

  schema_validation_enabled = false

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.squad_agent.id]
  }

  body = {
    properties = {
      environmentId = azurerm_container_app_environment.squad.id
      configuration = {
        replicaTimeout    = var.agent_job_config.timeout_seconds
        replicaRetryLimit = 0
        triggerType       = "Event"
        registries = [
          {
            server   = azurerm_container_registry.squad.login_server
            identity = azurerm_user_assigned_identity.squad_agent.id
          }
        ]
        eventTriggerConfig = {
          parallelism            = 1
          replicaCompletionCount = 1
          scale = {
            minExecutions   = 0
            maxExecutions   = var.agent_job_config.max_executions
            pollingInterval = 30
            rules = [
              {
                name = "queue-scaling"
                type = "azure-queue"
                metadata = {
                  queueName   = var.queue_name
                  queueLength = "1"
                  accountName = azurerm_storage_account.squad.name
                }
                identity = azurerm_user_assigned_identity.squad_agent.id
              }
            ]
          }
        }
      }
      template = {
        containers = [
          {
            name  = "squad-agent"
            image = local.agent_image
            resources = {
              cpu    = var.agent_job_config.cpu
              memory = var.agent_job_config.memory
            }
            env = [
              { name = "GITHUB_APP_ID", value = var.github_app_id },
              { name = "GITHUB_APP_INSTALLATION_ID", value = var.github_app_installation_id },
              { name = "KEY_VAULT_NAME", value = azurerm_key_vault.squad.name },
              { name = "KEY_VAULT_SECRET_NAME", value = "github-app-private-key" },
              { name = "COPILOT_TOKEN_SECRET_NAME", value = "copilot-pat" },
              { name = "QUEUE_NAME", value = var.queue_name },
              { name = "AZURE_STORAGE_ACCOUNT", value = azurerm_storage_account.squad.name },
              { name = "AZURE_CLIENT_ID", value = azurerm_user_assigned_identity.squad_agent.client_id }
            ]
          }
        ]
      }
    }
  }

  depends_on = [azapi_resource.work_queue, azurerm_private_endpoint.queue, azurerm_private_endpoint.vault]
}
