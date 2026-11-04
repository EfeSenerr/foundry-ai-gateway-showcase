<#
 Agents in Open WebUI, called AS THE USER (on-behalf-of) through APIM. Idempotent; demo tenant only.
   1. Gateway API app (portops-ai-gateway):
        - delegated permission Azure AI (https://ai.azure.com, user_impersonation) + tenant-wide admin consent
        - federated identity credential trusting the APIM system-assigned managed identity (secretless OBO client)
   2. RBAC: Foundry User on each department project for its budget group (sg-portops-team-<dept> -> project <dept>),
      so Foundry itself enforces which team may invoke which project's agents
   3. Open WebUI app (portops-openwebui): app roles Team.Ops/IT/CS assigned to the budget groups
      (Open WebUI syncs them as groups "Team.Ops" etc. via OAUTH_GROUP_CLAIM=roles)
   4. Persona display names for the synthetic demo users (UPNs unchanged)
 Uses Graph/ARM tokens for the PoC subscription's account (does not change the az default account).
#>
[CmdletBinding()]
param(
    [string]$ApiAppName = 'portops-ai-gateway',
    [string]$OpenWebUiAppName = 'portops-openwebui',
    [hashtable]$Personas = @{ ops = 'Lena Hoffmann (Ops)'; it = 'Jonas Weber (IT)'; cs = 'Mira Schulz (Customer Service)' }
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root '.env'
function Get-EnvValue([string]$Key) { ((Select-String -Path $envFile -Pattern "^$([regex]::Escape($Key))=(.*)$").Matches[0].Groups[1].Value).Trim() }
$sub = Get-EnvValue 'AZURE_SUBSCRIPTION_ID'; $rg = Get-EnvValue 'AZURE_RESOURCE_GROUP'
$apimName = Get-EnvValue 'APIM_NAME'; $foundry = Get-EnvValue 'FOUNDRY_ACCOUNT_NAME'

$gTok = az account get-access-token --subscription $sub --resource-type ms-graph --query accessToken -o tsv
$aTok = az account get-access-token --subscription $sub --query accessToken -o tsv
if (-not $gTok -or -not $aTok) { throw 'Could not get Graph/ARM tokens for the PoC subscription.' }

function Invoke-Api([string]$Token, [string]$Method, [string]$Uri, $Body) {
    $p = @{ Method = $Method; Uri = $Uri; Headers = @{ Authorization = "Bearer $Token" } }
    if ($null -ne $Body) { $p.ContentType = 'application/json'; $p.Body = ($Body | ConvertTo-Json -Depth 20) }
    for ($i = 1; $i -le 6; $i++) {
        try { return Invoke-RestMethod @p }
        catch {
            $code = $_.Exception.Response.StatusCode.value__
            if ($code -eq 409) { return $null }   # already exists (role assignment)
            if ($i -eq 6) { throw }; Start-Sleep 5
        }
    }
}
function G([string]$Method, [string]$Path, $Body) { Invoke-Api $gTok $Method "https://graph.microsoft.com/v1.0$Path" $Body }
function A([string]$Method, [string]$Path, $Body) { Invoke-Api $aTok $Method "https://management.azure.com$Path" $Body }
function Find([string]$Collection, [string]$Filter) { (G GET "$Collection`?`$filter=$([uri]::EscapeDataString($Filter))" $null).value | Select-Object -First 1 }

$tenantId = (G GET '/organization?$select=id' $null).value[0].id
$rgPath = "/subscriptions/$sub/resourceGroups/$rg/providers"

# ---------------------------------------------------------------- 1. gateway app: Azure AI permission + FIC
$api = Find '/applications' "displayName eq '$ApiAppName'"
$apiSp = Find '/servicePrincipals' "appId eq '$($api.appId)'"
$aiSp = Find '/servicePrincipals' "servicePrincipalNames/any(x:x eq 'https://ai.azure.com')"
$aiScopeId = ($aiSp.oauth2PermissionScopes | Where-Object value -eq 'user_impersonation').id

$rra = @($api.requiredResourceAccess | Where-Object { $_.resourceAppId -ne $aiSp.appId })
$rra += @{ resourceAppId = $aiSp.appId; resourceAccess = @(@{ id = $aiScopeId; type = 'Scope' }) }
G PATCH "/applications/$($api.id)" @{ requiredResourceAccess = $rra } | Out-Null

$grant = Find '/oauth2PermissionGrants' "clientId eq '$($apiSp.id)' and resourceId eq '$($aiSp.id)'"
if ($grant) { G PATCH "/oauth2PermissionGrants/$($grant.id)" @{ scope = 'user_impersonation' } | Out-Null }
else { G POST '/oauth2PermissionGrants' @{ clientId = $apiSp.id; consentType = 'AllPrincipals'; resourceId = $aiSp.id; scope = 'user_impersonation' } | Out-Null }
Write-Host "Gateway app: delegated https://ai.azure.com/user_impersonation consented"

$apimMi = (A GET "$rgPath/Microsoft.ApiManagement/service/$apimName`?api-version=2024-05-01" $null).identity.principalId
$fics = (G GET "/applications/$($api.id)/federatedIdentityCredentials" $null).value
if (-not ($fics | Where-Object name -eq 'apim-managed-identity')) {
    G POST "/applications/$($api.id)/federatedIdentityCredentials" @{
        name = 'apim-managed-identity'; issuer = "https://login.microsoftonline.com/$tenantId/v2.0"
        subject = $apimMi; audiences = @('api://AzureADTokenExchange')
        description = 'APIM system-assigned managed identity as client assertion for OBO (no secret)'
    } | Out-Null
}
Write-Host "Gateway app: federated credential for APIM managed identity $apimMi"

# ---------------------------------------------------------------- 2. RBAC: Foundry User per team project
$foundryUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'   # Foundry User (formerly Azure AI User)
foreach ($d in 'ops', 'it', 'cs') {
    $grp = Find '/groups' "displayName eq 'sg-portops-team-$d'"
    $scope = "$rgPath/Microsoft.CognitiveServices/accounts/$foundry/projects/$d"
    $raName = [guid]::new([Security.Cryptography.MD5]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes("$scope|$($grp.id)|$foundryUser"))).ToString()
    A PUT "$scope/providers/Microsoft.Authorization/roleAssignments/$raName`?api-version=2022-04-01" @{ properties = @{
        roleDefinitionId = "/subscriptions/$sub/providers/Microsoft.Authorization/roleDefinitions/$foundryUser"
        principalId = $grp.id; principalType = 'Group' } } | Out-Null
    Write-Host "RBAC: sg-portops-team-$d -> Foundry User on project $d"
}

# ---------------------------------------------------------------- 3. Open WebUI app roles -> Open WebUI groups
$ow = Find '/applications' "displayName eq '$OpenWebUiAppName'"
$roles = @($ow.appRoles)
foreach ($r in 'Ops', 'IT', 'CS') {
    if (-not ($roles | Where-Object value -eq "Team.$r")) {
        $roles += @{ id = [guid]::NewGuid().ToString(); value = "Team.$r"; displayName = "Team $r"; description = "Member of team $r (Open WebUI group Team.$r)"; allowedMemberTypes = @('User'); isEnabled = $true }
    }
}
G PATCH "/applications/$($ow.id)" @{ appRoles = $roles } | Out-Null
$ow = G GET "/applications/$($ow.id)" $null
$owSp = Find '/servicePrincipals' "appId eq '$($ow.appId)'"
$assigned = (G GET "/servicePrincipals/$($owSp.id)/appRoleAssignedTo" $null).value
foreach ($r in 'Ops', 'IT', 'CS') {
    $grp = Find '/groups' "displayName eq 'sg-portops-team-$($r.ToLower())'"
    $roleId = ($ow.appRoles | Where-Object value -eq "Team.$r").id
    if (-not ($assigned | Where-Object { $_.principalId -eq $grp.id -and $_.appRoleId -eq $roleId })) {
        G POST "/servicePrincipals/$($owSp.id)/appRoleAssignedTo" @{ principalId = $grp.id; resourceId = $owSp.id; appRoleId = $roleId } | Out-Null
    }
    Write-Host "Open WebUI: role Team.$r -> sg-portops-team-$($r.ToLower())"
}

# ---------------------------------------------------------------- 4. persona names for the synthetic users
foreach ($d in $Personas.Keys) {
    $u = Find '/users' "startswith(userPrincipalName,'portops-$d-user@')"
    if ($u) { G PATCH "/users/$($u.id)" @{ displayName = $Personas[$d] } | Out-Null; Write-Host "User $($u.userPrincipalName) -> '$($Personas[$d])'" }
}
Write-Host 'Done. Role assignments and app roles take effect at the next sign-in (fresh token).' -ForegroundColor Green
