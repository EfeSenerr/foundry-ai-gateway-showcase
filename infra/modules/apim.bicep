param suffix string
param location string
param tags object
@allowed(['BasicV2', 'StandardV2'])
param apimSku string
param publisherEmail string
param foundryAccountName string
param foundrySubdomain string
param appInsightsName string
param logAnalyticsWorkspaceId string
param entraTenantId string
param entraApiAppId string

var departments = [
  { id: 'ops', tpm: 20000, quota: 5000000 }
  { id: 'it', tpm: 2000, quota: 500000 }
  { id: 'cs', tpm: 10000, quota: 2500000 }
]
var productTemplate = loadTextContent('../policies/product-policy.xml')

resource appi 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: 'apim-portops-${suffix}'
  location: location
  tags: tags
  sku: { name: apimSku, capacity: 1 }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: publisherEmail
    publisherName: 'Portops PoC'
  }
}

// APIM managed identity -> Cognitive Services OpenAI User on the Foundry account
resource apimRa 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(foundry.id, apim.id, '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
  scope: foundry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd')
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource logger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'appinsights'
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appi.id
    credentials: { instrumentationKey: appi.properties.InstrumentationKey }
  }
}

// Backend + pool. Add more backends (other regions) to the pool services array.
resource backend 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = {
  parent: apim
  name: 'portops-openai-eu'
  properties: {
    type: 'Single'
    protocol: 'http'
    url: 'https://${foundrySubdomain}.openai.azure.com/openai'
    circuitBreaker: {
      rules: [
        {
          name: 'openaiBreakerRule'
          failureCondition: {
            count: 3
            interval: 'PT1M'
            statusCodeRanges: [{ min: 500, max: 599 }]
          }
          tripDuration: 'PT1M'
          acceptRetryAfter: true
        }
      ]
    }
  }
}

resource pool 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = {
  parent: apim
  name: 'portops-openai-pool'
  properties: {
    type: 'Pool'
    pool: {
      services: [
        { id: backend.id, weight: 1, priority: 1 }
      ]
    }
  }
}

resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'openai'
  properties: {
    displayName: 'Portops Azure OpenAI'
    path: 'openai'
    protocols: ['https']
    subscriptionRequired: true
    subscriptionKeyParameterNames: { header: 'api-key', query: 'api-key' }
    serviceUrl: 'https://${foundrySubdomain}.openai.azure.com/openai'
  }
}

var operations = [
  { name: 'chat-completions', display: 'Chat completions', url: '/deployments/{deployment-id}/chat/completions' }
  { name: 'completions', display: 'Completions', url: '/deployments/{deployment-id}/completions' }
  { name: 'embeddings', display: 'Embeddings', url: '/deployments/{deployment-id}/embeddings' }
]

resource ops 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for o in operations: {
  parent: api
  name: o.name
  properties: {
    displayName: o.display
    method: 'POST'
    urlTemplate: o.url
    templateParameters: [
      { name: 'deployment-id', type: 'string', required: true }
    ]
  }
}]

// ---- Named values (editable in the portal: APIM > Named values) ----
var namedValues = [
  { name: 'team-ops-enabled', value: 'true' }
  { name: 'team-it-enabled', value: 'true' }
  { name: 'team-cs-enabled', value: 'true' }
  { name: 'quota-team-ops', value: '5000000' }
  { name: 'quota-team-it', value: '500000' }
  { name: 'quota-team-cs', value: '2500000' }
  { name: 'quota-user-default', value: '200000' }
  { name: 'quota-user-power', value: '1000000' }
  { name: 'entra-tenant-id', value: entraTenantId }
  { name: 'entra-api-app-id', value: entraApiAppId }
]

resource nv 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = [for n in namedValues: {
  parent: apim
  name: n.name
  properties: {
    displayName: n.name
    value: n.value
    secret: false
  }
}]

// ---- Shared LLM core as policy fragments (fragments cannot hold <base/> or several sections) ----
var fragments = [
  { name: 'portops-llm-inbound', desc: 'LLM core inbound: chaos switch, model routing, token metric, managed identity, backend pool', xml: loadTextContent('../policies/fragments/llm-inbound.xml') }
  { name: 'portops-llm-backend', desc: 'LLM core backend: retry on 429/5xx', xml: loadTextContent('../policies/fragments/llm-backend.xml') }
  { name: 'portops-llm-outbound', desc: 'LLM core outbound headers', xml: loadTextContent('../policies/fragments/llm-outbound.xml') }
  { name: 'portops-llm-onerror', desc: 'LLM core on-error headers', xml: loadTextContent('../policies/fragments/llm-onerror.xml') }
  { name: 'portops-team-enabled', desc: 'Budget kill switch: 403 TeamBudgetExhausted when named value team-{team}-enabled is false', xml: loadTextContent('../policies/fragments/team-enabled.xml') }
]

resource frag 'Microsoft.ApiManagement/service/policyFragments@2024-05-01' = [for f in fragments: {
  parent: apim
  name: f.name
  properties: {
    description: f.desc
    format: 'rawxml'
    value: f.xml
  }
  dependsOn: [nv, pool]
}]

resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('../policies/api-policy.xml')
  }
  dependsOn: [pool, ops, frag]
}

resource products 'Microsoft.ApiManagement/service/products@2024-05-01' = [for d in departments: {
  parent: apim
  name: 'dept-${d.id}'
  properties: {
    displayName: 'Department ${d.id}'
    subscriptionRequired: true
    approvalRequired: false
    state: 'published'
  }
}]

resource productApis 'Microsoft.ApiManagement/service/products/apis@2024-05-01' = [for (d, i) in departments: {
  parent: products[i]
  name: api.name
}]

resource productPolicies 'Microsoft.ApiManagement/service/products/policies@2024-05-01' = [for (d, i) in departments: {
  parent: products[i]
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: replace(replace(productTemplate, '__TPM__', string(d.tpm)), '__QUOTA__', string(d.quota))
  }
}]

resource subs 'Microsoft.ApiManagement/service/subscriptions@2024-05-01' = [for (d, i) in departments: {
  parent: apim
  name: 'sub-dept-${d.id}'
  properties: {
    displayName: 'Department ${d.id}'
    scope: products[i].id
    state: 'active'
  }
  dependsOn: [productApis]
}]

resource apiDiag 'Microsoft.ApiManagement/service/apis/diagnostics@2024-06-01-preview' = {
  parent: api
  name: 'applicationinsights'
  properties: {
    loggerId: logger.id
    alwaysLog: 'allErrors'
    verbosity: 'information'
    logClientIp: true
    sampling: { samplingType: 'fixed', percentage: 100 }
    metrics: true
    frontend: {
      request: { headers: ['x-chaos', 'x-request-id'], body: { bytes: 0 } }
      response: { headers: ['apim-request-id', 'x-request-id', 'x-portops-department', 'x-portops-routed-model', 'x-portops-chaos', 'x-ratelimit-remaining-tokens'], body: { bytes: 0 } }
    }
    backend: {
      request: { headers: [], body: { bytes: 0 } }
      response: { headers: ['x-portops-routed-model'], body: { bytes: 0 } }
    }
  }
}

resource apiDiagAzMon 'Microsoft.ApiManagement/service/apis/diagnostics@2024-06-01-preview' = {
  parent: api
  name: 'azuremonitor'
  properties: {
    loggerId: '${apim.id}/loggers/azuremonitor'
    alwaysLog: 'allErrors'
    verbosity: 'information'
    logClientIp: true
    sampling: { samplingType: 'fixed', percentage: 100 }
    frontend: {
      request: { headers: ['x-chaos'], body: { bytes: 0 } }
      response: { headers: ['apim-request-id', 'x-request-id', 'x-portops-department', 'x-portops-routed-model', 'x-portops-chaos'], body: { bytes: 0 } }
    }
    backend: {
      request: { headers: [], body: { bytes: 0 } }
      response: { headers: [], body: { bytes: 0 } }
    }
    largeLanguageModel: {
      logs: 'enabled'
      requests: { messages: 'all', maxSizeInBytes: 32768 }
      responses: { messages: 'all', maxSizeInBytes: 32768 }
    }
  }
}

resource diag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'to-law'
  scope: apim
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      { category: 'GatewayLogs', enabled: true }
      { category: 'GatewayLlmLogs', enabled: true }
    ]
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
}

output loggerId string = logger.id
output apimName string = apim.name
output gatewayUrl string = apim.properties.gatewayUrl
