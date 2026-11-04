<#
 Deploys the "PortOps AI Gateway - Cost & Operations" Azure Workbook into the PoC resource group.
 Reads .env (AZURE_SUBSCRIPTION_ID, AZURE_RESOURCE_GROUP, LOG_ANALYTICS_WORKSPACE_ID) - never prints keys.
 Usage: .\scripts\deploy-workbook.ps1 [-WorkspaceName log-portops-<suffix>]
#>
param([string]$WorkspaceName = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envMap = @{}
Get-Content (Join-Path $root '.env') | ForEach-Object {
    if ($_ -match '^\s*([A-Z0-9_]+)=(.*)$') { $envMap[$Matches[1]] = $Matches[2].Trim() }
}
$sub = $envMap['AZURE_SUBSCRIPTION_ID']; $rg = $envMap['AZURE_RESOURCE_GROUP']
if (-not $WorkspaceName) {
    $WorkspaceName = az monitor log-analytics workspace list --subscription $sub -g $rg --query "[?customerId=='$($envMap['LOG_ANALYTICS_WORKSPACE_ID'])'].name | [0]" -o tsv
}
if (-not $WorkspaceName) { throw 'Could not resolve the Log Analytics workspace name; pass -WorkspaceName.' }

# Regenerate the workbook JSON if python + the demo venv are available (the JSON in git is already current).
$py = Join-Path $root 'demo\.venv\Scripts\python.exe'
if (Test-Path $py) { & $py (Join-Path $root 'infra\workbook\build_workbook.py') | Out-Host }

Write-Host "Deploying workbook to $rg (workspace $WorkspaceName)..."
$out = az deployment group create --subscription $sub -g $rg -n portops-workbook `
    -f (Join-Path $root 'infra\workbook\workbook.bicep') -p workspaceName=$WorkspaceName `
    --query properties.outputs -o json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Deployment failed' }
$id = $out.workbookId.value
$url = "https://portal.azure.com/#@/resource$id/workbook"

# Store the workbook id in .env so the UI can link to it.
$envPath = Join-Path $root '.env'
$lines = @(Get-Content $envPath | Where-Object { $_ -notmatch '^WORKBOOK_ID=' }) + "WORKBOOK_ID=$id"
Set-Content -Path $envPath -Value $lines -Encoding utf8
Write-Host "`nWorkbook resource : $id"
Write-Host "Open in portal    : $url"
Write-Host "Or: portal.azure.com > Monitor > Workbooks > (resource group $rg) > 'PortOps AI Gateway - Cost & Operations'"
