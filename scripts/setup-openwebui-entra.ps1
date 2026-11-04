<#
 Entra app registration for a local (or Container Apps) Open WebUI that signs users in with Entra ID and forwards the
 user's access token to the APIM API openai-entra-v1 (Open WebUI connection auth_type "system_oauth").
   - web app  portops-openwebui  (redirect <OpenWebUiUrl>/oauth/microsoft/callback)
   - delegated permissions: Graph openid/profile/email/offline_access + portops-ai-gateway/Gateway.Use, admin-consented
   - client secret (30 days) -> openwebui\.env only (git-ignored), never printed
 Users get their team from the EXISTING budget groups sg-portops-team-* (app roles on portops-ai-gateway).
 Uses a Graph token for the PoC subscription's account (does not change the az default account).
#>
[CmdletBinding()]
param(
    [string]$AppName = 'portops-openwebui',
    [string[]]$OpenWebUiUrls = @('http://localhost:3000'),
    [string]$ApiAppName = 'portops-ai-gateway',
    [switch]$RotateSecret
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$pocEnv = Join-Path $root '.env'
$owuiEnv = Join-Path $root 'openwebui\.env'
$graph = 'https://graph.microsoft.com/v1.0'

$sub = ((Select-String -Path $pocEnv -Pattern '^AZURE_SUBSCRIPTION_ID=(.*)$').Matches[0].Groups[1].Value).Trim()
$gwUrl = ((Select-String -Path $pocEnv -Pattern '^APIM_GATEWAY_URL=(.*)$').Matches[0].Groups[1].Value).Trim()
$token = az account get-access-token --subscription $sub --resource-type ms-graph --query accessToken -o tsv
if ($LASTEXITCODE -ne 0) { throw 'Could not get a Graph token for the PoC tenant.' }

function G([string]$Method, [string]$Path, $Body) {
    $p = @{ Method = $Method; Uri = "$graph$Path"; Headers = @{ Authorization = "Bearer $token" } }
    if ($null -ne $Body) { $p.ContentType = 'application/json'; $p.Body = ($Body | ConvertTo-Json -Depth 20) }
    for ($i = 1; $i -le 6; $i++) {
        try { return Invoke-RestMethod @p } catch { if ($i -eq 6) { throw }; Start-Sleep 5 }
    }
}
function Find([string]$Collection, [string]$Filter) {
    (G GET "$Collection`?`$filter=$([uri]::EscapeDataString($Filter))" $null).value | Select-Object -First 1
}
function Set-EnvValue([string]$File, [string]$Key, [string]$Value) {
    $lines = @(); if (Test-Path $File) { $lines = @(Get-Content $File) }
    $found = $false
    $lines = @($lines | ForEach-Object { if ($_ -match "^$([regex]::Escape($Key))=") { $found = $true; "$Key=$Value" } else { $_ } })
    if (-not $found) { $lines += "$Key=$Value" }
    [IO.File]::WriteAllLines($File, [string[]]$lines, (New-Object Text.UTF8Encoding($false)))
}
function Get-EnvValue([string]$File, [string]$Key) {
    if (-not (Test-Path $File)) { return $null }
    $m = Select-String -Path $File -Pattern "^$([regex]::Escape($Key))=(.*)$"
    if ($m) { $m.Matches[0].Groups[1].Value } else { $null }
}

$tenantId = (G GET '/organization?$select=id' $null).value[0].id
$graphAppId = '00000003-0000-0000-c000-000000000000'
$graphScopes = @{ openid = '37f7f235-527c-4136-accd-4a02d197296e'; profile = '14dad69e-099b-42c9-810b-d002981feec1'
                  email = '64a6cdd6-aab1-4aaf-94b8-3cc8405e90d0'; offline_access = '7427e0e9-2fba-42fe-b0c0-848c9e6a8182' }

$api = Find '/applications' "displayName eq '$ApiAppName'"
if (-not $api) { throw "API app $ApiAppName not found; run setup-entra.ps1 first." }
$scopeId = ($api.api.oauth2PermissionScopes | Where-Object value -eq 'Gateway.Use').id

$app = Find '/applications' "displayName eq '$AppName'"
if (-not $app) { Write-Host "Creating app registration $AppName"; $app = G POST '/applications' @{ displayName = $AppName; signInAudience = 'AzureADMyOrg' } }
G PATCH "/applications/$($app.id)" @{
    web = @{ redirectUris = @($OpenWebUiUrls | ForEach-Object { "$($_.TrimEnd('/'))/oauth/microsoft/callback" }) }
    requiredResourceAccess = @(
        @{ resourceAppId = $graphAppId; resourceAccess = @($graphScopes.Values | ForEach-Object { @{ id = $_; type = 'Scope' } }) }
        @{ resourceAppId = $api.appId; resourceAccess = @(@{ id = $scopeId; type = 'Scope' }) }
    )
} | Out-Null

$sp = Find '/servicePrincipals' "appId eq '$($app.appId)'"
if (-not $sp) { $sp = G POST '/servicePrincipals' @{ appId = $app.appId } }

# admin consent (tenant-wide) for Graph sign-in scopes + Gateway.Use
foreach ($grant in @(
        @{ appId = $graphAppId; scope = 'openid profile email offline_access' }
        @{ appId = $api.appId; scope = 'Gateway.Use' })) {
    $resSp = Find '/servicePrincipals' "appId eq '$($grant.appId)'"
    $existing = Find '/oauth2PermissionGrants' "clientId eq '$($sp.id)' and resourceId eq '$($resSp.id)'"
    if ($existing) { G PATCH "/oauth2PermissionGrants/$($existing.id)" @{ scope = $grant.scope } | Out-Null }
    else { G POST '/oauth2PermissionGrants' @{ clientId = $sp.id; consentType = 'AllPrincipals'; resourceId = $resSp.id; scope = $grant.scope } | Out-Null }
}

New-Item -ItemType Directory -Force (Split-Path $owuiEnv) | Out-Null
if ($RotateSecret -or -not (Get-EnvValue $owuiEnv 'MICROSOFT_CLIENT_SECRET')) {
    foreach ($old in ((G GET "/applications/$($app.id)?`$select=passwordCredentials" $null).passwordCredentials | Where-Object displayName -eq 'openwebui-poc')) {
        G POST "/applications/$($app.id)/removePassword" @{ keyId = $old.keyId } | Out-Null
    }
    $pw = G POST "/applications/$($app.id)/addPassword" @{ passwordCredential = @{ displayName = 'openwebui-poc'; endDateTime = (Get-Date).ToUniversalTime().AddDays(30).ToString('o') } }
    Set-EnvValue $owuiEnv 'MICROSOFT_CLIENT_SECRET' $pw.secretText
    Write-Host 'New client secret written to openwebui\.env'
}
if (-not (Get-EnvValue $owuiEnv 'WEBUI_SECRET_KEY')) {
    Set-EnvValue $owuiEnv 'WEBUI_SECRET_KEY' ([Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }) -as [byte[]]))
}
Set-EnvValue $owuiEnv 'MICROSOFT_CLIENT_ID' $app.appId
Set-EnvValue $owuiEnv 'MICROSOFT_CLIENT_TENANT_ID' $tenantId
Set-EnvValue $owuiEnv 'MICROSOFT_OAUTH_SCOPE' "openid email profile offline_access api://$ApiAppName/Gateway.Use"
Set-EnvValue $owuiEnv 'APIM_GATEWAY_URL' $gwUrl

Write-Host "App $AppName ($($app.appId)) ready; redirect(s): $($OpenWebUiUrls -join ', ')" -ForegroundColor Green
Write-Host 'Team membership = existing groups sg-portops-team-ops|it|cs (Entra ID > Groups).'
