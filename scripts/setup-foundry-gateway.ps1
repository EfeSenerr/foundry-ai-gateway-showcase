<#
.SYNOPSIS
  Routes Foundry model calls (Playground + prompt agents) through the APIM AI gateway.
  Idempotent: creates/updates two connections in each department project (ops/it/cs),
  each carrying THAT department's APIM subscription key (read via listSecrets at runtime,
  never written to disk).

  apim-gateway       category ApiManagement  -> Agent Service ("bring your own AI gateway"),
                     agent model name = apim-gateway/<deployment>
  apim-gateway-aoai  category AzureOpenAI    -> Foundry Playground / Azure OpenAI-style clients

.PARAMETER Departments  Departments to configure (default ops, it, cs).
#>
param(
    [string[]] $Departments = @('ops', 'it', 'cs'),
    [string] $ResourceGroup,
    [string] $AgentConnectionPrefix = 'apim-gateway',
    [string] $PlaygroundConnectionPrefix = 'apim-gateway-aoai',
    [string[]] $Models = @('gpt-4.1-mini', 'gpt-4.1'),
    [string] $InferenceApiVersion = '2024-10-21'
)
$ErrorActionPreference = 'Stop'

# settings from .env (non-secret)
$envFile = Join-Path $PSScriptRoot '..\.env'
$cfg = @{}
Get-Content $envFile | ForEach-Object { if ($_ -match '^\s*([A-Z0-9_]+)=(.*)$') { $cfg[$Matches[1]] = $Matches[2].Trim() } }
if (-not $ResourceGroup) { $ResourceGroup = $cfg['AZURE_RESOURCE_GROUP'] }
$sub = $cfg['AZURE_SUBSCRIPTION_ID']; $foundry = $cfg['FOUNDRY_ACCOUNT_NAME']; $apim = $cfg['APIM_NAME']
$gateway = "https://$apim.azure-api.net/openai"   # API path `openai`; Foundry appends /deployments/<id>/chat/completions
$arm = 'https://management.azure.com'
$apimId = "/subscriptions/$sub/resourceGroups/$ResourceGroup/providers/Microsoft.ApiManagement/service/$apim"
$token = az account get-access-token --resource $arm --query accessToken -o tsv
$hdr = @{ Authorization = "Bearer $token"; 'Content-Type' = 'application/json' }

$staticModels = @($Models | ForEach-Object {
        @{ name = $_; properties = @{ model = @{ name = $_; version = ''; format = 'OpenAI' } } }
    })
$modelsJson = ConvertTo-Json -InputObject $staticModels -Depth 6 -Compress

foreach ($d in $Departments) {
    $secretsUrl = "$arm/subscriptions/$sub/resourceGroups/$ResourceGroup/providers/Microsoft.ApiManagement/service/$apim/subscriptions/sub-dept-$d/listSecrets?api-version=2024-05-01"
    $key = (Invoke-RestMethod -Method Post -Uri $secretsUrl -Headers $hdr).primaryKey
    if (-not $key) { throw "No key for APIM subscription sub-dept-$d" }

    $defs = @(
        @{ name = "$AgentConnectionPrefix-$d"; props = @{
                category = 'ApiManagement'; target = $gateway; authType = 'ApiKey'
                credentials = @{ key = $key }; isSharedToAll = $false
                metadata = @{ deploymentInPath = 'true'; inferenceAPIVersion = $InferenceApiVersion
                    models = $modelsJson; department = $d }
            }
        }
        @{ name = "$PlaygroundConnectionPrefix-$d"; props = @{
                category = 'AzureOpenAI'; target = $gateway; authType = 'ApiKey'
                credentials = @{ key = $key }; isSharedToAll = $false
                metadata = @{ ApiType = 'Azure'; ApiVersion = $InferenceApiVersion
                    DeploymentApiVersion = $InferenceApiVersion; ResourceId = $apimId; department = $d }
            }
        }
    )
    foreach ($c in $defs) {
        $url = "$arm/subscriptions/$sub/resourceGroups/$ResourceGroup/providers/Microsoft.CognitiveServices/accounts/$foundry/projects/$d/connections/$($c.name)?api-version=2025-06-01"
        $body = @{ properties = $c.props } | ConvertTo-Json -Depth 10
        $r = Invoke-RestMethod -Method Put -Uri $url -Headers $hdr -Body $body
        Write-Host ("[{0}] {1,-18} {2,-14} -> {3}" -f $d, $r.name, $r.properties.category, $r.properties.target) -ForegroundColor Cyan
    }
    $key = $null
}
Write-Host "`nDone. Agent model name: $AgentConnectionPrefix-<dept>/<deployment> (e.g. $AgentConnectionPrefix-ops/gpt-4.1-mini)" -ForegroundColor Green
