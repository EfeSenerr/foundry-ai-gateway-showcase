[CmdletBinding()]
param(
    [string]$Suffix,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$rg = $null
if ($Suffix) { $rg = "rg-portops-poc-$Suffix" }
elseif (Test-Path "$root\.env") {
    $m = Select-String -Path "$root\.env" -Pattern '^AZURE_RESOURCE_GROUP=(.*)$'
    if ($m) { $rg = $m.Matches[0].Groups[1].Value }
}
if (-not $rg) { throw 'Provide -Suffix or have a .env with AZURE_RESOURCE_GROUP' }

if (-not $Force) {
    $a = Read-Host "Delete resource group '$rg' and purge the Foundry account? (yes/no)"
    if ($a -ne 'yes') { Write-Host 'Aborted.'; return }
}

$acct = az cognitiveservices account list -g $rg --query "[?kind=='AIServices'].{n:name,l:location}" -o json | ConvertFrom-Json
az group delete -n $rg --yes
foreach ($x in $acct) {
    Write-Host "Purging soft-deleted Foundry account $($x.n)"
    az cognitiveservices account purge -g $rg -n $x.n -l $x.l
}
# APIM v2 is not soft-deleted in the same way as classic tiers; purge if present
az apim deletedservice list --query "[?contains(name,'portops')].{n:name,l:location}" -o json | ConvertFrom-Json | ForEach-Object {
    az apim deletedservice purge --service-name $_.n --location $_.l
}
if (Test-Path "$root\.env") { Remove-Item "$root\.env" }
Write-Host 'Teardown complete.'
