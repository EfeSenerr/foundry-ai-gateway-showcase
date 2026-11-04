<#
 Adds the OpenAI v1-compatible API "openai-entra-v1" (path entra/v1) to the EXISTING PoC APIM. Additive only:
   - named value quota-owui-user (created once; existing value is kept unless -UserQuota is passed)
   - API openai-entra-v1 + operations (POST /chat/completions, GET /models) + policy infra\policies\openai-entra-v1-policy.xml
   - diagnostics copied from the PoC API openai-entra (App Insights token metrics + LLM logs)
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

if ($Remove) {
    Arm DELETE '/apis/openai-entra-v1' $null | Out-Null
    Arm DELETE '/namedValues/quota-owui-user' $null | Out-Null
    Write-Host 'Removed API openai-entra-v1 and named value quota-owui-user.'; return
}

# named value
$nv = $null; try { $nv = Arm GET '/namedValues/quota-owui-user' $null } catch {}
if (-not $nv -or $UserQuota) {
    $val = if ($UserQuota) { $UserQuota } else { '200000' }
    Arm PUT '/namedValues/quota-owui-user' @{ properties = @{ displayName = 'quota-owui-user'; value = $val; secret = $false } } | Out-Null
    Write-Host "Named value quota-owui-user = $val"
}

# API (serviceUrl taken from the PoC entra API; the policy routes to the backend pool anyway)
$src = Arm GET '/apis/openai-entra' $null
Arm PUT '/apis/openai-entra-v1' @{ properties = @{
    displayName = 'Portops OpenAI v1 (Entra user token, Open WebUI)'; path = 'entra/v1'; protocols = @('https')
    subscriptionRequired = $false; serviceUrl = $src.properties.serviceUrl } } | Out-Null

$ops = @(
    @{ name = 'chat-completions'; display = 'Chat completions'; method = 'POST'; url = '/chat/completions' }
    @{ name = 'list-models'; display = 'List models'; method = 'GET'; url = '/models' }
)
foreach ($o in $ops) {
    Arm PUT "/apis/openai-entra-v1/operations/$($o.name)" @{ properties = @{ displayName = $o.display; method = $o.method; urlTemplate = $o.url } } | Out-Null
}

$xml = Get-Content -Raw (Join-Path $PSScriptRoot '..\infra\policies\openai-entra-v1-policy.xml')
Arm PUT '/apis/openai-entra-v1/policies/policy' @{ properties = @{ format = 'rawxml'; value = $xml } } | Out-Null

foreach ($d in 'applicationinsights', 'azuremonitor') {
    try {
        $sd = Arm GET "/apis/openai-entra/diagnostics/$d" $null 'api-version=2024-06-01-preview'
        Arm PUT "/apis/openai-entra-v1/diagnostics/$d" @{ properties = $sd.properties } 'api-version=2024-06-01-preview' | Out-Null
        Write-Host "Diagnostics $d copied"
    } catch { Write-Warning "Diagnostics $d not copied: $($_.Exception.Message)" }
}
Write-Host "Ready: $(Get-PocValue 'APIM_GATEWAY_URL')/entra/v1  (POST /chat/completions, GET /models)" -ForegroundColor Green
