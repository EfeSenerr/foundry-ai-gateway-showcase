targetScope = 'subscription'

@description('Short lowercase suffix (5 chars) used in resource names')
@minLength(3)
@maxLength(8)
param suffix string

param location string = 'swedencentral'

@description('Object ID of the signed-in user (gets Foundry data-plane roles)')
param userObjectId string

@description('Email of the signed-in user (budget + action group recipient)')
param userEmail string

@description('APIM publisher email')
param publisherEmail string = userEmail

@allowed(['DataZoneStandard', 'GlobalStandard', 'Standard'])
param modelSku string = 'DataZoneStandard'

@description('Capacity in thousands of tokens per minute')
param smallModelCapacity int = 100
param largeModelCapacity int = 50

@allowed(['BasicV2', 'StandardV2'])
param apimSku string = 'BasicV2'

@description('Entra tenant id (setup-entra.ps1 writes ENTRA_TENANT_ID to .env)')
param entraTenantId string

@description('App id of the portops-ai-gateway API app registration (ENTRA_API_APP_ID)')
param entraApiAppId string

param deployBudget bool = true
param budgetAmount int = 100
param budgetStartDate string = utcNow('yyyy-MM-01')

var tags = {
  workload: 'portops-poc'
  env: 'demo'
}
var rgName = 'rg-portops-poc-${suffix}'

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: rgName
  location: location
  tags: tags
}

module monitoring 'modules/monitoring.bicep' = {
  scope: rg
  name: 'monitoring'
  params: {
    suffix: suffix
    location: location
    tags: tags
  }
}

module foundry 'modules/foundry.bicep' = {
  scope: rg
  name: 'foundry'
  params: {
    suffix: suffix
    location: location
    tags: tags
    userObjectId: userObjectId
    modelSku: modelSku
    smallModelCapacity: smallModelCapacity
    largeModelCapacity: largeModelCapacity
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceResourceId
  }
}

module apim 'modules/apim.bicep' = {
  scope: rg
  name: 'apim'
  params: {
    suffix: suffix
    location: location
    tags: tags
    apimSku: apimSku
    publisherEmail: publisherEmail
    foundryAccountName: foundry.outputs.accountName
    foundrySubdomain: foundry.outputs.subdomain
    appInsightsName: monitoring.outputs.appInsightsName
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceResourceId
    entraTenantId: entraTenantId
    entraApiAppId: entraApiAppId
  }
}

module apimEntra 'modules/apim-entra.bicep' = {
  scope: rg
  name: 'apim-entra'
  params: {
    apimName: apim.outputs.apimName
    foundrySubdomain: foundry.outputs.subdomain
    loggerId: apim.outputs.loggerId
  }
}

module apps 'modules/apps.bicep' = {
  scope: rg
  name: 'apps'
  params: {
    suffix: suffix
    location: location
    tags: tags
    logAnalyticsWorkspaceName: monitoring.outputs.workspaceName
  }
}

module budget 'modules/budget.bicep' = if (deployBudget) {
  scope: rg
  name: 'budget'
  params: {
    suffix: suffix
    tags: tags
    userEmail: userEmail
    budgetAmount: budgetAmount
    startDate: budgetStartDate
  }
}

param deployBudgetEnforcer bool = true

module budgetEnforcer 'modules/budget-enforcer.bicep' = if (deployBudgetEnforcer) {
  scope: rg
  name: 'budget-enforcer'
  params: {
    suffix: suffix
    location: location
    tags: tags
    userEmail: userEmail
    apimName: apim.outputs.apimName
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceResourceId
    budgetStartDate: budgetStartDate
  }
}

output resourceGroupName string = rg.name
output foundryAccountName string = foundry.outputs.accountName
output foundryEndpoint string = foundry.outputs.endpoint
output projectEndpoints object = foundry.outputs.projectEndpoints
output apimName string = apim.outputs.apimName
output apimGatewayUrl string = apim.outputs.gatewayUrl
output logAnalyticsWorkspaceId string = monitoring.outputs.workspaceCustomerId
output appInsightsName string = monitoring.outputs.appInsightsName
output containerAppsEnvName string = apps.outputs.envName
output acrName string = apps.outputs.acrName
output acrLoginServer string = apps.outputs.acrLoginServer
output mcpIdentityId string = apps.outputs.identityId
output modelSkuUsed string = modelSku
