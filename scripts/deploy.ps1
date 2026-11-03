[CmdletBinding()]
param(
    [string]$Suffix,
    [string]$Location = 'swedencentral',
    [ValidateSet('DataZoneStandard', 'GlobalStandard', 'Standard')]
    [string]$ModelSku = 'DataZoneStandard',
    [ValidateSet('BasicV2', 'StandardV2')]
    [string]$ApimSku = 'BasicV2',
    [bool]$DeployBudget = $true,
    [bool]$DeployBudgetEnforcer = $true
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$subId = az account show --query id -o tsv
if (-not $Suffix) {
    $sha = [System.Security.Cryptography.SHA1]::Create()
    $hash = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes("portops-$subId"))
    $Suffix = (-join ($hash | ForEach-Object { '{0:x2}' -f $_ })).Substring(0, 5)
}
$user = az ad signed-in-user show --query "{id:id,mail:mail,upn:userPrincipalName}" -o json | ConvertFrom-Json
$email = if ($user.mail) { $user.mail } else { $user.upn }
function Get-EnvValue([string]$Key) {
    if (-not (Test-Path "$root\.env")) { return $null }
    $m = Select-String -Path "$root\.env" -Pattern "^$([regex]::Escape($Key))=(.*)$"
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}
$entraTenantId = Get-EnvValue 'ENTRA_TENANT_ID'
$entraApiAppId = Get-EnvValue 'ENTRA_API_APP_ID'
if (-not $entraTenantId -or -not $entraApiAppId) { throw 'ENTRA_TENANT_ID / ENTRA_API_APP_ID missing in .env - run .\scripts\setup-entra.ps1 first.' }
$start = (Get-Date).ToString('yyyy-MM-01')
Write-Host "Deploying portops PoC: suffix=$Suffix location=$Location modelSku=$ModelSku apim=$ApimSku"

az deployment sub create --name "portops-$Suffix" --location $Location `
    --template-file "$root\infra\main.bicep" `
    --parameters suffix=$Suffix location=$Location userObjectId=$($user.id) userEmail=$email `
    modelSku=$ModelSku apimSku=$ApimSku deployBudget=$DeployBudget deployBudgetEnforcer=$DeployBudgetEnforcer budgetStartDate=$start entraTenantId=$entraTenantId entraApiAppId=$entraApiAppId --only-show-errors -o none
if ($LASTEXITCODE -ne 0) { throw 'Deployment failed' }

$o = az deployment sub show --name "portops-$Suffix" --query properties.outputs -o json | ConvertFrom-Json
$rg = $o.resourceGroupName.value
$apim = $o.apimName.value

function Get-ApimKey($dept) {
    $uri = "https://management.azure.com/subscriptions/$subId/resourceGroups/$rg/providers/Microsoft.ApiManagement/service/$apim/subscriptions/sub-dept-$dept/listSecrets?api-version=2024-05-01"
    az rest --method post --uri $uri --query primaryKey -o tsv
}

$existingMcp = ''
if (Test-Path "$root\.env") {
    $m = Select-String -Path "$root\.env" -Pattern '^MCP_URL=(.*)$'
    if ($m) { $existingMcp = $m.Matches[0].Groups[1].Value }
}

$p = $o.projectEndpoints.value
$lines = @(
    "AZURE_SUBSCRIPTION_ID=$subId",
    "AZURE_RESOURCE_GROUP=$rg",
    "AZURE_LOCATION=$Location",
    "FOUNDRY_ACCOUNT_NAME=$($o.foundryAccountName.value)",
    "FOUNDRY_ENDPOINT=$($o.foundryEndpoint.value)",
    "PROJECT_ENDPOINT_OPS=$($p.ops)",
    "PROJECT_ENDPOINT_IT=$($p.it)",
    "PROJECT_ENDPOINT_CS=$($p.cs)",
    "APIM_NAME=$apim",
    "APIM_GATEWAY_URL=$($o.apimGatewayUrl.value)",
    "APIM_KEY_OPS=$(Get-ApimKey 'ops')",
    "APIM_KEY_IT=$(Get-ApimKey 'it')",
    "APIM_KEY_CS=$(Get-ApimKey 'cs')",
    "LOG_ANALYTICS_WORKSPACE_ID=$($o.logAnalyticsWorkspaceId.value)",
    "MCP_URL=$existingMcp",
    "MODEL_SMALL=gpt-4.1-mini",
    "MODEL_LARGE=gpt-4.1",
    "APP_INSIGHTS_NAME=$($o.appInsightsName.value)",
    "CONTAINER_APPS_ENV=$($o.containerAppsEnvName.value)",
    "ACR_NAME=$($o.acrName.value)",
    "ACR_LOGIN_SERVER=$($o.acrLoginServer.value)",
    "MCP_IDENTITY_ID=$($o.mcpIdentityId.value)"
)
# keep the values written by setup-entra.ps1 (Entra ids, demo-user UPNs/passwords)
$keep = @()
if (Test-Path "$root\.env") { $keep = @(Get-Content "$root\.env" | Where-Object { $_ -match '^(ENTRA_|PORTOPS_TESTUSER_|MCP_GATEWAY_URL=|MCP_KEY_)' }) }
Set-Content -Path "$root\.env" -Value ($lines + $keep) -Encoding utf8
Write-Host "Done. Wrote $root\.env (git-ignored). Gateway: $($o.apimGatewayUrl.value)"
