terraform {
  # Write-only arguments (azapi sensitive_body) need 1.11; ephemeral
  # variables need 1.10. Together they keep the VM admin password out of
  # both the plan file and the state.
  required_version = ">= 1.11"

  required_providers {
    azapi = {
      source  = "azure/azapi"
      version = "~> 2.13.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.2"
    }
  }

  # Partial config; values come from -backend-config in deploy-demo-vm.yml.
  backend "azurerm" {}
}

# Subscription, tenant, client and OIDC settings come from the ARM_* env
# vars set by the workflow.
provider "azapi" {}

provider "azurerm" {
  features {}

  # The pipeline identity is scoped to the demo resource group and cannot
  # register resource providers at subscription scope.
  resource_provider_registrations = "none"
}
