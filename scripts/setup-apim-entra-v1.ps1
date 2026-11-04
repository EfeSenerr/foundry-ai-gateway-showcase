<#
 Adds the Open WebUI APIs to the EXISTING PoC APIM. Additive only:
   - openai-entra-v1      path entra/v1         OpenAI v1 chat completions for raw models (infra\policies\openai-entra-v1-policy.xml)
   - openai-entra-agents  path entra/agents/v1  Responses API for Foundry agents, invoked as the user via OBO (infra\policies\openai-entra-agents-policy.xml)
   - named values quota-owui-user (kept unless -UserQuota is passed) and foundry-services-host
   - diagnostics copied from the PoC API openai-entra (App Insights token metrics + LLM logs)
 The agents API needs scripts\setup-openwebui-agents.ps1 (OBO federated credential, Foundry RBAC).
 Uses an ARM token for the PoC subscription (does not change the az default account).
 Remove: .\setup-apim-entra-v1.ps1 -Remove
#>
[CmdletBinding()]
param(
    [string]$PocEnv = (Join-Path $PSScriptRoot '..\.env'),
    [string]$UserQuota,
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'

function Get-PocValue([string]$Key) {
    $m = Select-String -Path $PocEnv -Pattern "^$([regex]::Escape($Key))=(.*)$"
    if (-not $m) { throw "$Key missing in $PocEnv" }
    $m.Matches[0].Groups[1].Value.Trim()
}
$sub = Get-PocValue 'AZURE_SUBSCRIPTION_ID'; $rg = Get-PocValue 'AZURE_RESOURCE_GROUP'; $apim = Get-PocValue 'APIM_NAME'
$token = az account get-access-token --subscription $sub --query accessToken -o tsv
if ($LASTEXITCODE -ne 0) { throw 'Could not get an ARM token for the PoC subscription.' }
$base = "https://management.azure.com/subscriptions/$sub/resourceGroups/$rg/providers/Microsoft.ApiManagement/service/$apim"
$v = 'api-version=2024-05-01'

function Arm([string]$Method, [string]$Path, $Body, [string]$Ver = $v) {
    $p = @{ Method = $Method; Uri = "$base$Path`?$Ver"; Headers = @{ Authorization = "Bearer $token"; 'If-Match' = '*' } }
    if ($null -ne $Body) { $p.ContentType = 'application/json'; $p.Body = ($Body | ConvertTo-Json -Depth 30) }
    Invoke-RestMethod @p
}

$apis = @(
    @{ name = 'openai-entra-v1'; display = 'Portops OpenAI v1 (Entra user token, Open WebUI)'; path = 'entra/v1'; policy = 'openai-entra-v1-policy.xml'
       ops = @(@{ name = 'chat-completions'; display = 'Chat completions'; method = 'POST'; url = '/chat/completions' }
               @{ name = 'responses'; display = 'Responses'; method = 'POST'; url = '/responses' }
               @{ name = 'list-models'; display = 'List models'; method = 'GET'; url = '/models' }) }
    @{ name = 'openai-entra-agents'; display = 'Portops Foundry agents (Entra user token, OBO, Open WebUI)'; path = 'entra/agents/v1'; policy = 'openai-entra-agents-policy.xml'
       ops = @(@{ name = 'responses'; display = 'Responses (agent)'; method = 'POST'; url = '/responses' }
               @{ name = 'list-models'; display = 'List agents'; method = 'GET'; url = '/models' }) }
)

if ($Remove) {
    foreach ($a in $apis) { Arm DELETE "/apis/$($a.name)" $null | Out-Null }
    foreach ($n in 'quota-owui-user', 'foundry-services-host') { Arm DELETE "/namedValues/$n" $null | Out-Null }
    Write-Host 'Removed the Open WebUI APIs and named values.'; return
}

# named values
$nv = $null; try { $nv = Arm GET '/namedValues/quota-owui-user' $null } catch {}
if (-not $nv -or $UserQuota) {
    $val = if ($UserQuota) { $UserQuota } else { '200000' }
    Arm PUT '/namedValues/quota-owui-user' @{ properties = @{ displayName = 'quota-owui-user'; value = $val; secret = $false } } | Out-Null
    Write-Host "Named value quota-owui-user = $val"
}
$foundryHost = "$(Get-PocValue 'FOUNDRY_ACCOUNT_NAME').services.ai.azure.com"
Arm PUT '/namedValues/foundry-services-host' @{ properties = @{ displayName = 'foundry-services-host'; value = $foundryHost; secret = $false } } | Out-Null

# APIs (serviceUrl taken from the PoC entra API; the policies set the backend themselves)
$src = Arm GET '/apis/openai-entra' $null
foreach ($a in $apis) {
    Arm PUT "/apis/$($a.name)" @{ properties = @{ displayName = $a.display; path = $a.path; protocols = @('https')
        subscriptionRequired = $false; serviceUrl = $src.properties.serviceUrl } } | Out-Null
    foreach ($o in $a.ops) {
        Arm PUT "/apis/$($a.name)/operations/$($o.name)" @{ properties = @{ displayName = $o.display; method = $o.method; urlTemplate = $o.url } } | Out-Null
    }
    $xml = Get-Content -Raw (Join-Path $PSScriptRoot "..\infra\policies\$($a.policy)")
    Arm PUT "/apis/$($a.name)/policies/policy" @{ properties = @{ format = 'rawxml'; value = $xml } } | Out-Null
    foreach ($d in 'applicationinsights', 'azuremonitor') {
        try {
            $sd = Arm GET "/apis/openai-entra/diagnostics/$d" $null 'api-version=2024-06-01-preview'
            Arm PUT "/apis/$($a.name)/diagnostics/$d" @{ properties = $sd.properties } 'api-version=2024-06-01-preview' | Out-Null
        } catch { Write-Warning "Diagnostics $d not copied to $($a.name): $($_.Exception.Message)" }
    }
    Write-Host "Ready: $(Get-PocValue 'APIM_GATEWAY_URL')/$($a.path)" -ForegroundColor Green
}