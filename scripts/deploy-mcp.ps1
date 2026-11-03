#requires -Version 5.1
<#
Builds the MCP server image in ACR (remote build), deploys/updates Container App ca-portops-mcp,
writes MCP_URL to the repo-root .env, and runs the smoke test against the deployed URL.
Also (idempotent): sets the MCP backend key (Container App secret + APIM secret named value), deploys the APIM MCP
governance layer (infra/modules/apim-mcp.bicep), writes MCP_GATEWAY_URL + MCP_KEY_OPS/IT/CS to .env and creates the
Foundry project connections mcp-gateway-<team> (team key as api-key header) used by the agents.
Waits (polls every 2 min, up to 40 min) until the infra resource group, ACR and Container Apps env exist.
#>
[CmdletBinding()]
param(
    [string]$AppName = 'ca-portops-mcp',
    [int]$PollMinutes = 2,
    [int]$MaxWaitMinutes = 40,
    [switch]$SkipSmoke
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$envFile = Join-Path $root '.env'

function Read-DotEnv([string]$path) {
    $h = @{}
    foreach ($l in Get-Content $path) {
        if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') { $h[$Matches[1]] = $Matches[2].Trim('"') }
    }
    $h
}

function Set-DotEnvValue([string]$path, [string]$key, [string]$value) {
    $lines = @(Get-Content $path)
    $found = $false
    $lines = $lines | ForEach-Object { if ($_ -match "^\s*$key\s*=") { $found = $true; "$key=$value" } else { $_ } }
    if (-not $found) { $lines += "$key=$value" }
    Set-Content -Path $path -Value $lines -Encoding utf8
}

function Get-InfraState {
    if (-not (Test-Path $envFile)) { return $null }
    $cfg = Read-DotEnv $envFile
    $rg = $cfg['AZURE_RESOURCE_GROUP']
    if (-not $rg) { return $null }
    az group show -g $rg -o none 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    $acr = az acr list -g $rg --query '[0].name' -o tsv 2>$null
    $cae = az containerapp env list -g $rg --query '[0].name' -o tsv 2>$null
    if (-not $acr -or -not $cae) { return $null }
    [pscustomobject]@{ ResourceGroup = $rg; Acr = $acr; Env = $cae; Location = $cfg['AZURE_LOCATION'] }
}

$deadline = (Get-Date).AddMinutes($MaxWaitMinutes)
while (-not ($infra = Get-InfraState)) {
    if ((Get-Date) -gt $deadline) { throw "Infra not ready after $MaxWaitMinutes minutes (need .env with AZURE_RESOURCE_GROUP, an ACR and a Container Apps environment)." }
    Write-Host "Infra not ready yet; retrying in $PollMinutes min..."
    Start-Sleep -Seconds ($PollMinutes * 60)
}
$rg = $infra.ResourceGroup
Write-Host "Using RG=$rg ACR=$($infra.Acr) ENV=$($infra.Env)"

az extension add --name containerapp --upgrade --only-show-errors 2>$null | Out-Null

$tag = Get-Date -Format 'yyyyMMddHHmmss'
$image = "portops-mcp:$tag"
Write-Host "Building $image in ACR (remote)..."
az acr build -r $infra.Acr -t $image (Join-Path $root 'mcp') --no-logs -o none
if ($LASTEXITCODE -ne 0) { throw 'az acr build failed' }
$server = az acr show -n $infra.Acr -g $rg --query loginServer -o tsv
$fullImage = "$server/$image"

az containerapp show -n $AppName -g $rg -o none 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host 'Updating container app...'
    az containerapp update -n $AppName -g $rg --image $fullImage --min-replicas 1 -o none
} else {
    Write-Host 'Creating container app...'
    az containerapp create -n $AppName -g $rg --environment $infra.Env --image $fullImage `
        --ingress external --target-port 8000 --transport auto --min-replicas 1 --max-replicas 2 `
        --cpu 0.5 --memory 1Gi --system-assigned `
        --registry-server $server --registry-identity system `
        --tags project=portops -o none
}
if ($LASTEXITCODE -ne 0) { throw 'containerapp deploy failed' }

$fqdn = az containerapp show -n $AppName -g $rg --query properties.configuration.ingress.fqdn -o tsv

# Backend protection (part C): the MCP server only accepts calls carrying x-mcp-key; only the APIM gateway knows it.
# The key lives in a Container App secret + an APIM secret named value (mcp-backend-key); never in files. Re-runs reuse it.
$mcpKey = az containerapp secret show -n $AppName -g $rg --secret-name mcp-backend-key --query value -o tsv 2>$null
if ($LASTEXITCODE -ne 0 -or -not $mcpKey) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $mcpKey = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
    Write-Host 'Generated a new MCP backend key.'
}
az containerapp secret set -n $AppName -g $rg --secrets "mcp-backend-key=$mcpKey" -o none
if ($LASTEXITCODE -ne 0) { throw 'containerapp secret set failed' }
az containerapp update -n $AppName -g $rg --set-env-vars 'MCP_API_KEY=secretref:mcp-backend-key' -o none
if ($LASTEXITCODE -ne 0) { throw 'containerapp env update failed' }

$mcpUrl = "https://$fqdn/mcp"
Set-DotEnvValue $envFile 'MCP_URL' $mcpUrl
Write-Host "MCP_URL=$mcpUrl written to .env (direct, needs x-mcp-key; use MCP_GATEWAY_URL through APIM)"

# APIM: MCP APIs + per-team products/keys + policy, backed by the key above
$cfg = Read-DotEnv $envFile
$apimName = $cfg['APIM_NAME']
if ($apimName) {
    Write-Host "Deploying APIM MCP governance (infra/modules/apim-mcp.bicep) to $apimName..."
    $keyFile = Join-Path $root 'infra\.mcp-key.tmp.json'
    @{ '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'; contentVersion = '1.0.0.0'
       parameters = @{ mcpBackendKey = @{ value = $mcpKey } } } | ConvertTo-Json -Depth 5 | Set-Content $keyFile -Encoding utf8
    try {
        az deployment group create -g $rg -n portops-mcp-governance -f (Join-Path $root 'infra\modules\apim-mcp.bicep') `
            -p apimName=$apimName mcpFqdn=$fqdn "@$keyFile" -o none
        if ($LASTEXITCODE -ne 0) { throw 'APIM MCP governance deployment failed' }
    } finally { Remove-Item $keyFile -ErrorAction SilentlyContinue }

    $subId = az account show --query id -o tsv
    $keys = @{}
    foreach ($t in 'ops', 'it', 'cs') {
        $uri = "https://management.azure.com/subscriptions/$subId/resourceGroups/$rg/providers/Microsoft.ApiManagement/service/$apimName/subscriptions/sub-mcp-$t/listSecrets?api-version=2024-05-01"
        $k = az rest --method post --uri $uri --query primaryKey -o tsv
        $keys[$t] = $k
        Set-DotEnvValue $envFile "MCP_KEY_$($t.ToUpper())" $k
    }
    $gwMcp = "$($cfg['APIM_GATEWAY_URL'].TrimEnd('/'))/mcp"
    Set-DotEnvValue $envFile 'MCP_GATEWAY_URL' $gwMcp
    Write-Host 'MCP_GATEWAY_URL + MCP_KEY_OPS/IT/CS written to .env'

    # Foundry agents authenticate to the gateway through a CustomKeys project connection (header api-key = team key);
    # `headers` on an MCP tool is not persisted in agent definitions.
    if ($cfg['FOUNDRY_ACCOUNT_NAME']) {
        $armTok = az account get-access-token --resource https://management.azure.com --query accessToken -o tsv
        $hdr = @{ Authorization = "Bearer $armTok"; 'Content-Type' = 'application/json' }
        foreach ($t in 'ops', 'it', 'cs') {
            $url = "https://management.azure.com/subscriptions/$subId/resourceGroups/$rg/providers/Microsoft.CognitiveServices/accounts/$($cfg['FOUNDRY_ACCOUNT_NAME'])/projects/$t/connections/mcp-gateway-$t`?api-version=2025-06-01"
            $body = @{ properties = @{ category = 'CustomKeys'; target = "$gwMcp/$t"; authType = 'CustomKeys'
                    credentials = @{ keys = @{ 'api-key' = $keys[$t] } }; isSharedToAll = $false; metadata = @{ department = $t } } } | ConvertTo-Json -Depth 6
            $r = Invoke-RestMethod -Method Put -Uri $url -Headers $hdr -Body $body
            Write-Host "[$t] Foundry connection $($r.name) -> $($r.properties.target)"
        }
    }
} else {
    Write-Warning 'APIM_NAME missing in .env - skipped APIM MCP governance (run deploy.ps1 first).'
}

if (-not $SkipSmoke) {
    $py = Join-Path $root 'mcp\.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { $py = 'python' }
    $base = "https://$fqdn"
    for ($i = 1; $i -le 10; $i++) {
        try { Invoke-RestMethod "$base/health" -TimeoutSec 10 | Out-Null; break } catch { Start-Sleep 10 }
    }
    & $py (Join-Path $root 'mcp\tests\smoke_client.py') $base $mcpKey
    if ($LASTEXITCODE -ne 0) { throw 'Smoke test failed' }
}

