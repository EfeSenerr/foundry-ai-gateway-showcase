param suffix string
param location string
param tags object
param logAnalyticsWorkspaceName string

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: 'cae-portops-${suffix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'acrportops${suffix}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource uai 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-portops-mcp-${suffix}'
  location: location
  tags: tags
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, uai.id, 'acrpull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: uai.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

output envName string = env.name
output acrName string = acr.name
output acrLoginServer string = acr.properties.loginServer
output identityId string = uai.id
