// MCP governance through the APIM gateway (part C). Deployed by scripts/deploy-mcp.ps1 (it owns the backend key),
// independent from main.bicep so a main.bicep redeploy never overwrites the secret named value.
// One native APIM "MCP server" API (type 'mcp', API version 2024-06-01-preview, works on BasicV2) per team,
// each pointing at the scoped MCP endpoint https://<container-app>/<team>/mcp.
param apimName string
param mcpFqdn string

@secure()
@description('Shared secret the APIM gateway injects as x-mcp-key; the Container App rejects calls without it')
param mcpBackendKey string

param loggerName string = 'appinsights'

@description('Calls per minute and caller (subscription or user) for tools/call')
param rateLimitPerMinute int = 30

// Mirror of mcp/departments.py DEPARTMENT_TOOLS. Editable at runtime in the portal: APIM > Named values > mcp-tools-<team>.
var teams = [
  { id: 'ops', tools: 'get_vessel_schedule,get_berth_plan,get_crane_assignments,track_container,get_yard_position,check_customs_hold,list_truck_slots,book_truck_slot' }
  { id: 'it', tools: 'search_incident_logs,get_incident,summarize_errors' }
  { id: 'cs', tools: 'track_container,get_yard_position,check_customs_hold,list_truck_slots,book_truck_slot' }
]
var policyTemplate = loadTextContent('../policies/mcp-api-policy.xml')

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimName
}

resource logger 'Microsoft.ApiManagement/service/loggers@2024-06-01-preview' existing = {
  parent: apim
  name: loggerName
}

resource nvKey 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'mcp-backend-key'
  properties: {
    displayName: 'mcp-backend-key'
    value: mcpBackendKey
    secret: true
  }
}

resource nvRate 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'mcp-rate-limit-per-min'
  properties: {
    displayName: 'mcp-rate-limit-per-min'
    value: string(rateLimitPerMinute)
    secret: false
  }
}

resource nvTools 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = [for t in teams: {
  parent: apim
  name: 'mcp-tools-${t.id}'
  properties: {
    displayName: 'mcp-tools-${t.id}'
    value: t.tools
    secret: false
  }
}]

resource backends 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = [for t in teams: {
  parent: apim
  name: 'portops-mcp-${t.id}'
  properties: {
    description: 'Portops MCP server, ${t.id} scope'
    url: 'https://${mcpFqdn}/${t.id}/mcp'
    protocol: 'http'
  }
}]

resource apis 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = [for (t, i) in teams: {
  parent: apim
  name: 'mcp-${t.id}'
  properties: {
    type: 'mcp'
    displayName: 'Portops MCP (${t.id})'
    description: 'Governed MCP endpoint for team ${t.id}'
    path: 'mcp/${t.id}'
    protocols: ['https']
    // Auth (key or Entra) is decided in the policy so a wrong-team caller gets an explicit 403
    subscriptionRequired: false
    subscriptionKeyParameterNames: { header: 'api-key', query: 'api-key' }
    // Native-MCP properties are missing from the published Bicep types (BCP037) but accepted by the API
    #disable-next-line BCP037
    backendId: backends[i].name
    #disable-next-line BCP037
    mcpProperties: { transportType: 'streamable' }
  }
}]

resource apiPolicies 'Microsoft.ApiManagement/service/apis/policies@2024-06-01-preview' = [for (t, i) in teams: {
  parent: apis[i]
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: replace(policyTemplate, '__TEAM__', t.id)
  }
  dependsOn: [nvKey, nvRate, nvTools]
}]

// No request/response bodies are logged for MCP (Learn: response payload bytes must be 0 so streaming is not buffered)
resource diagAi 'Microsoft.ApiManagement/service/apis/diagnostics@2024-06-01-preview' = [for (t, i) in teams: {
  parent: apis[i]
  name: 'applicationinsights'
  properties: {
    loggerId: logger.id
    alwaysLog: 'allErrors'
    verbosity: 'information'
    logClientIp: true
    sampling: { samplingType: 'fixed', percentage: 100 }
    metrics: true
    frontend: {
      request: { headers: ['x-request-id'], body: { bytes: 0 } }
      response: { headers: ['x-portops-mcp-team', 'x-portops-mcp-result'], body: { bytes: 0 } }
    }
    backend: {
      request: { headers: [], body: { bytes: 0 } }
      response: { headers: [], body: { bytes: 0 } }
    }
  }
}]

resource diagAzMon 'Microsoft.ApiManagement/service/apis/diagnostics@2024-06-01-preview' = [for (t, i) in teams: {
  parent: apis[i]
  name: 'azuremonitor'
  properties: {
    loggerId: '${apim.id}/loggers/azuremonitor'
    alwaysLog: 'allErrors'
    verbosity: 'information'
    logClientIp: true
    sampling: { samplingType: 'fixed', percentage: 100 }
    frontend: {
      request: { headers: [], body: { bytes: 0 } }
      response: { headers: ['x-portops-mcp-team', 'x-portops-mcp-result'], body: { bytes: 0 } }
    }
    backend: {
      request: { headers: [], body: { bytes: 0 } }
      response: { headers: [], body: { bytes: 0 } }
    }
  }
}]

// ---- Per-team products with subscription keys (every MCP API is in each product; the policy enforces team == path) ----
resource products 'Microsoft.ApiManagement/service/products@2024-06-01-preview' = [for t in teams: {
  parent: apim
  name: 'mcp-${t.id}'
  properties: {
    displayName: 'MCP ${t.id}'
    description: 'Access to the governed MCP endpoint of team ${t.id}'
    subscriptionRequired: true
    approvalRequired: false
    state: 'published'
  }
}]

resource productApis 'Microsoft.ApiManagement/service/products/apis@2024-06-01-preview' = [for (t, i) in teams: {
  parent: products[i]
  name: 'mcp-ops'
  dependsOn: [apis]
}]

resource productApisIt 'Microsoft.ApiManagement/service/products/apis@2024-06-01-preview' = [for (t, i) in teams: {
  parent: products[i]
  name: 'mcp-it'
  dependsOn: [apis]
}]

resource productApisCs 'Microsoft.ApiManagement/service/products/apis@2024-06-01-preview' = [for (t, i) in teams: {
  parent: products[i]
  name: 'mcp-cs'
  dependsOn: [apis]
}]

resource subs 'Microsoft.ApiManagement/service/subscriptions@2024-06-01-preview' = [for (t, i) in teams: {
  parent: apim
  name: 'sub-mcp-${t.id}'
  properties: {
    displayName: 'MCP ${t.id}'
    scope: products[i].id
    state: 'active'
  }
  dependsOn: [productApis, productApisIt, productApisCs]
}]

output apiPaths array = [for t in teams: 'mcp/${t.id}']
