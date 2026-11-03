param suffix string
param location string
param tags object

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-portops-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-portops-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
    // Needed so llm-emit-token-metric dimensions (Department, Model) can be split in Metrics Explorer.
    #disable-next-line BCP037
    CustomMetricsOptedInType: 'WithDimensions'
  }
}

output workspaceResourceId string = law.id
output workspaceName string = law.name
output workspaceCustomerId string = law.properties.customerId
output appInsightsName string = appi.name
