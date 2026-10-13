targetScope = 'resourceGroup'

param sandboxGroupName string
@allowed(['swedencentral'])
param location string
@minLength(2)
@maxLength(2)
param presenterIds array
param consumerPrincipalId string
@minLength(3)
@maxLength(3)
param assignmentNames array

resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-07-01' = {
  name: sandboxGroupName
  location: location
  properties: {}
}

resource firstPresenter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: assignmentNames[0]
  scope: sandboxGroup
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'c24cf47c-5077-412d-a19c-45202126392c')
    principalId: presenterIds[0]
    principalType: 'User'
  }
}
resource secondPresenter 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: assignmentNames[1]
  scope: sandboxGroup
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'c24cf47c-5077-412d-a19c-45202126392c')
    principalId: presenterIds[1]
    principalType: 'User'
  }
}
resource consumerContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: assignmentNames[2]
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')
    principalId: consumerPrincipalId
    principalType: 'ServicePrincipal'
  }
}
