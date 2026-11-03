// Identity-based API: path entra/openai, Entra bearer token instead of subscription key.
// Requires the policy fragments + named values created by apim.bicep (module dependency enforced in main.bicep).
param apimName string
param foundrySubdomain string
param loggerId string

resource apim 'Microsoft.ApiManagement/service@2024-05-01' existing = {
  name: apimName
}

resource api 'Microsoft.ApiManagement/service/apis@2024-05-01' = {
  parent: apim
  name: 'openai-entra'
  properties: {
    displayName: 'Portops Azure OpenAI (Entra identity)'
    path: 'entra/openai'
    protocols: ['https']
    subscriptionRequired: false
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

resource apiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = {
  parent: api
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('../policies/entra-api-policy.xml')
  }
  dependsOn: [ops]
}

resource apiDiag 'Microsoft.ApiManagement/service/apis/diagnostics@2024-06-01-preview' = {
  parent: api
  name: 'applicationinsights'
  properties: {
    loggerId: loggerId
    alwaysLog: 'allErrors'
    verbosity: 'information'
    logClientIp: true
    sampling: { samplingType: 'fixed', percentage: 100 }
    metrics: true
    frontend: {
      request: { headers: ['x-chaos', 'x-request-id'], body: { bytes: 0 } }
      response: { headers: ['apim-request-id', 'x-request-id', 'x-portops-department', 'x-portops-team', 'x-portops-user', 'x-portops-routed-model', 'x-portops-chaos', 'x-ratelimit-remaining-tokens'], body: { bytes: 0 } }
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
      response: { headers: ['apim-request-id', 'x-request-id', 'x-portops-department', 'x-portops-team', 'x-portops-user', 'x-portops-routed-model', 'x-portops-chaos'], body: { bytes: 0 } }
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
