targetScope = 'subscription'

@description('Private, scope-approved resource group name. No public defaults.')
param resourceGroupName string
@description('Private, scope-approved SandboxGroup name.')
param sandboxGroupName string
@allowed(['swedencentral'])
param location string
@description('Two approved user object IDs, in reviewed order.')
@minLength(2)
@maxLength(2)
param presenterIds array
@description('Existing consumer service principal object ID, not its client ID.')
param consumerPrincipalId string
@description('Three UUIDv5 names independently checked against scope|principal|role.')
@minLength(3)
@maxLength(3)
param assignmentNames array

// Official RG module; no telemetry, lock, AVM IAM submodule or other workload.
module rg 'br/public:avm/res/resources/resource-group:0.4.4' = {
  name: 'aca-online-rg'
  params: {
    name: resourceGroupName
    location: location
    enableTelemetry: false
  }
}

// The preview group has no published AVM module. Native typed Bicep is intentional.
module sandbox './sandbox.bicep' = {
  name: 'aca-online-sandbox'
  scope: resourceGroup(resourceGroupName)
  params: {
    sandboxGroupName: sandboxGroupName
    location: location
    presenterIds: presenterIds
    consumerPrincipalId: consumerPrincipalId
    assignmentNames: assignmentNames
  }
  dependsOn: [rg]
}
