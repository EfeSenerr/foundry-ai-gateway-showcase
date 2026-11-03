param suffix string
param location string
param tags object
param userObjectId string
param modelSku string
param smallModelCapacity int
param largeModelCapacity int
param logAnalyticsWorkspaceId string

var accountName = 'aif-portops-${suffix}'
var departments = ['ops', 'it', 'cs']

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: accountName
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: accountName
    allowProjectManagement: true
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

@batchSize(1)
resource projects 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = [for d in departments: {
  parent: account
  name: d
  location: location
  tags: union(tags, { department: d })
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'portops-${d}'
    description: 'Terminal Ops PoC project for ${d} department'
  }
}]

resource small 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: 'gpt-4.1-mini'
  sku: { name: modelSku, capacity: smallModelCapacity }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1-mini', version: '2025-04-14' }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
  dependsOn: [projects]
}

resource large 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: 'gpt-4.1'
  sku: { name: modelSku, capacity: largeModelCapacity }
  properties: {
    model: { format: 'OpenAI', name: 'gpt-4.1', version: '2025-04-14' }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
  dependsOn: [small]
}

resource diag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-law'
  scope: account
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [{ categoryGroup: 'allLogs', enabled: true }]
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
}

// Azure AI User + Cognitive Services OpenAI User for the signed-in user
var userRoles = [
  '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
]
resource userRa 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in userRoles: {
  name: guid(account.id, userObjectId, r)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r)
    principalId: userObjectId
    principalType: 'User'
  }
}]

output accountName string = account.name
output subdomain string = accountName
output endpoint string = 'https://${accountName}.cognitiveservices.azure.com/'
output projectEndpoints object = {
  ops: 'https://${accountName}.services.ai.azure.com/api/projects/ops'
  it: 'https://${accountName}.services.ai.azure.com/api/projects/it'
  cs: 'https://${accountName}.services.ai.azure.com/api/projects/cs'
}
