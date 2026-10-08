terraform {
  required_version = ">= 1.10"

  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.13.0"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.37"
    }
  }

  # Partial config; values come from -backend-config in deploy-squad-on-aca.yml.
  backend "azurerm" {}
}

provider "azapi" {}

provider "azurerm" {
  features {}
  storage_use_azuread = true

  # The pipeline identity is resource-group scoped and cannot register providers.
  resource_provider_registrations = "none"
}
