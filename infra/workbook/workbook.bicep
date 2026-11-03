targetScope = 'resourceGroup'

@description('Name of the existing Log Analytics workspace the workbook reads from.')
param workspaceName string

param location string = resourceGroup().location

@description('Resource name suffix (default: derived from the workspace name log-portops-<suffix>).')
param suffix string = replace(workspaceName, 'log-portops-', '')

@description('Workbook display name.')
param displayName string = 'PortOps AI Gateway – Cost & Operations'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: workspaceName
}

var raw = loadTextContent('portops-ai-gateway.workbook.json')
var serialized = replace(replace(replace(replace(raw, '__WORKSPACE_ID__', workspace.id), '__SUBSCRIPTION_ID__', subscription().subscriptionId), '__RESOURCE_GROUP__', resourceGroup().name), '__SUFFIX__', suffix)

// Stable GUID so redeploys update the same workbook.
resource workbook 'Microsoft.Insights/workbooks@2023-06-01' = {
  name: guid(resourceGroup().id, 'portops-ai-gateway-workbook')
  location: location
  kind: 'shared'
  tags: {
    workload: 'portops-poc'
  }
  properties: {
    displayName: displayName
    category: 'workbook'
    sourceId: workspace.id
    serializedData: serialized
    version: '1.0'
  }
}

output workbookId string = workbook.id
output workbookName string = workbook.name
