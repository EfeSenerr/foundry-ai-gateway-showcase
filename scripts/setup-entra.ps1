<#
 Idempotent Entra ID setup for the identity-based limits (part A):
   - API app registration  portops-ai-gateway  (scope Gateway.Use, app roles Team.Ops/IT/CS + Tier.Power)
   - security groups sg-portops-team-ops|it|cs, sg-portops-power-users (= "budget groups") assigned to the app roles
   - synthetic demo users portops-{ops,it,cs}-user (passwords go ONLY to the git-ignored .env, never printed)
   - public client app  portops-demo-client  (device-code / ROPC for demo users, admin-consented for Gateway.Use)
 Writes ENTRA_* / PORTOPS_TESTUSER_* into .env. Run BEFORE deploy.ps1. Needs a signed-in az user that may manage apps/users/groups.
#>
[CmdletBinding()]
param(
    [string]$ApiAppName = 'portops-ai-gateway',
    [string]$ClientAppName = 'portops-demo-client'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root '.env'
$graph = 'https://graph.microsoft.com/v1.0'
$azureCliAppId = '04b07795-8ddb-461a-bbee-02f9e1bf7b46'
$bodyFile = Join-Path $root '.entra-body.json'

function Invoke-Graph([string]$Method, [string]$Path, $Body) {
    $args2 = @('rest', '--method', $Method, '--uri', "$graph$Path", '--only-show-errors', '--headers', 'Content-Type=application/json')
    if ($null -ne $Body) {
        [IO.File]::WriteAllText($bodyFile, ($Body | ConvertTo-Json -Depth 20 -Compress), (New-Object Text.UTF8Encoding($false)))
        $args2 += @('--body', "@$bodyFile")
    }
    try {
        $out = & az @args2 2>&1
        if ($LASTEXITCODE -ne 0) { throw ($out | Out-String) }
    } finally { Remove-Item $bodyFile -ErrorAction SilentlyContinue }
    if ($out) { return ($out | Out-String | ConvertFrom-Json) }
}

# Graph is eventually consistent right after creates: retry a few times.
function Invoke-GraphRetry([string]$Method, [string]$Path, $Body) {
    for ($i = 1; $i -le 8; $i++) {
        try { return Invoke-Graph $Method $Path $Body }
        catch { if ($i -eq 8) { throw }; Start-Sleep -Seconds 5 }
    }
}

function Find-One([string]$Collection, [string]$Filter) {
    $r = Invoke-Graph GET "$Collection`?`$filter=$([uri]::EscapeDataString($Filter))" $null
    if ($r.value.Count -gt 0) { return $r.value[0] }
    return $null
}

function Set-EnvValue([string]$Key, [string]$Value) {
    $lines = @()
    if (Test-Path $envFile) { $lines = @(Get-Content $envFile) }
    $found = $false
    $lines = $lines | ForEach-Object { if ($_ -match "^$([regex]::Escape($Key))=") { $found = $true; "$Key=$Value" } else { $_ } }
    if (-not $found) { $lines += "$Key=$Value" }
    [IO.File]::WriteAllLines($envFile, [string[]]$lines, (New-Object Text.UTF8Encoding($false)))
}

function Get-EnvValue([string]$Key) {
    if (-not (Test-Path $envFile)) { return $null }
    $m = Select-String -Path $envFile -Pattern "^$([regex]::Escape($Key))=(.*)$"
    if ($m) { return $m.Matches[0].Groups[1].Value }
    return $null
}

function New-RandomPassword {
    $chars = (48..57) + (65..90) + (97..122)
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $b = New-Object byte[] 24; $rng.GetBytes($b)
    $core = -join ($b | ForEach-Object { [char]$chars[$_ % $chars.Count] })
    return "Pw$core!9a"   # guarantees upper/lower/digit/symbol complexity
}

# ---------------------------------------------------------------- tenant / admin
$me = Invoke-Graph GET '/me?$select=id,userPrincipalName' $null
$org = (Invoke-Graph GET '/organization?$select=id,verifiedDomains' $null).value[0]
$tenantId = $org.id
$domain = ($org.verifiedDomains | Where-Object { $_.isDefault }).name
Write-Host "Tenant $tenantId (default domain $domain); signed in as $($me.userPrincipalName)"

# ---------------------------------------------------------------- API app registration
$roleDefs = @(
    @{ value = 'Team.Ops';    name = 'Ops team budget group';   desc = 'Calls are charged to the Ops team budget.' }
    @{ value = 'Team.IT';     name = 'IT team budget group';    desc = 'Calls are charged to the IT team budget.' }
    @{ value = 'Team.CS';     name = 'CS team budget group';    desc = 'Calls are charged to the CS team budget.' }
    @{ value = 'Tier.Power';  name = 'Power user tier';         desc = 'Higher per-user token limits.' }
)

$app = Find-One '/applications' "displayName eq '$ApiAppName'"
if (-not $app) {
    Write-Host "Creating app registration $ApiAppName"
    $app = Invoke-Graph POST '/applications' @{ displayName = $ApiAppName; signInAudience = 'AzureADMyOrg' }
}
$appObjId = $app.id; $apiAppId = $app.appId

# keep existing ids (idempotent), mint new GUIDs only for missing items
$scopeId = ($app.api.oauth2PermissionScopes | Where-Object { $_.value -eq 'Gateway.Use' } | Select-Object -First 1).id
if (-not $scopeId) { $scopeId = [guid]::NewGuid().ToString() }
$roles = foreach ($r in $roleDefs) {
    $existing = $app.appRoles | Where-Object { $_.value -eq $r.value } | Select-Object -First 1
    @{
        id = if ($existing) { $existing.id } else { [guid]::NewGuid().ToString() }
        value = $r.value; displayName = $r.name; description = $r.desc
        allowedMemberTypes = @('User', 'Application'); isEnabled = $true
    }
}
$patch = @{
    identifierUris = @("api://$ApiAppName")
    api = @{
        requestedAccessTokenVersion = 2
        oauth2PermissionScopes = @(@{
            id = $scopeId; value = 'Gateway.Use'; type = 'User'; isEnabled = $true
            adminConsentDisplayName = 'Use the Portops AI gateway'
            adminConsentDescription = 'Call the Portops AI gateway on behalf of the signed-in user.'
            userConsentDisplayName = 'Use the Portops AI gateway'
            userConsentDescription = 'Call the Portops AI gateway on your behalf.'
        })
        preAuthorizedApplications = @(@{ appId = $azureCliAppId; delegatedPermissionIds = @($scopeId) })
    }
    appRoles = @($roles)
}
# a scope/role that is being kept must be sent as-is; first PATCH sets scope + roles, preauth in a second call if the first rejects it
try { Invoke-Graph PATCH "/applications/$appObjId" $patch | Out-Null }
catch {
    $pre = $patch.api.preAuthorizedApplications; $patch.api.Remove('preAuthorizedApplications')
    Invoke-Graph PATCH "/applications/$appObjId" $patch | Out-Null
    $patch.api.preAuthorizedApplications = $pre
    Invoke-Graph PATCH "/applications/$appObjId" @{ api = @{ preAuthorizedApplications = $pre } } | Out-Null
}
$app = Invoke-Graph GET "/applications/$appObjId" $null
Write-Host "API app: $apiAppId (api://$ApiAppName)"

# ---------------------------------------------------------------- API service principal (assignment required)
$sp = Find-One '/servicePrincipals' "appId eq '$apiAppId'"
if (-not $sp) { $sp = Invoke-GraphRetry POST '/servicePrincipals' @{ appId = $apiAppId } }
Invoke-Graph PATCH "/servicePrincipals/$($sp.id)" @{ appRoleAssignmentRequired = $true } | Out-Null
$roleIds = @{}
foreach ($r in $app.appRoles) { $roleIds[$r.value] = $r.id }

# ---------------------------------------------------------------- groups + role assignments
$groupDefs = @(
    @{ name = 'sg-portops-team-ops';    role = 'Team.Ops' }
    @{ name = 'sg-portops-team-it';     role = 'Team.IT' }
    @{ name = 'sg-portops-team-cs';     role = 'Team.CS' }
    @{ name = 'sg-portops-power-users'; role = 'Tier.Power' }
)
$groups = @{}
$assigned = (Invoke-Graph GET "/servicePrincipals/$($sp.id)/appRoleAssignedTo?`$top=200" $null).value
foreach ($g in $groupDefs) {
    $grp = Find-One '/groups' "displayName eq '$($g.name)'"
    if (-not $grp) {
        Write-Host "Creating group $($g.name)"
        $grp = Invoke-Graph POST '/groups' @{ displayName = $g.name; mailNickname = $g.name; mailEnabled = $false; securityEnabled = $true; description = "Portops budget group ($($g.role))" }
    }
    $groups[$g.name] = $grp.id
    $has = $assigned | Where-Object { $_.principalId -eq $grp.id -and $_.appRoleId -eq $roleIds[$g.role] }
    if (-not $has) {
        Invoke-GraphRetry POST "/servicePrincipals/$($sp.id)/appRoleAssignedTo" @{ principalId = $grp.id; resourceId = $sp.id; appRoleId = $roleIds[$g.role] } | Out-Null
        Write-Host "Assigned $($g.role) -> $($g.name)"
    }
}

function Add-GroupMember([string]$GroupName, [string]$UserId) {
    $gid = $groups[$GroupName]
    $members = (Invoke-Graph GET "/groups/$gid/members?`$select=id" $null).value
    if (-not ($members | Where-Object { $_.id -eq $UserId })) {
        Invoke-GraphRetry POST "/groups/$gid/members/`$ref" @{ '@odata.id' = "$graph/directoryObjects/$UserId" } | Out-Null
    }
}

# ---------------------------------------------------------------- synthetic test users
foreach ($team in 'ops', 'it', 'cs') {
    $T = $team.ToUpper()
    $upn = "portops-$team-user@$domain"
    $pw = Get-EnvValue "PORTOPS_TESTUSER_${T}_PASSWORD"
    $u = Find-One '/users' "userPrincipalName eq '$upn'"
    if (-not $u) {
        if (-not $pw) { $pw = New-RandomPassword }
        Write-Host "Creating user $upn"
        $u = Invoke-Graph POST '/users' @{
            accountEnabled = $true; displayName = "Portops $T demo user"; mailNickname = "portops-$team-user"
            userPrincipalName = $upn
            passwordProfile = @{ password = $pw; forceChangePasswordNextSignIn = $false }
        }
    } elseif (-not $pw) {
        # user exists but we lost the password: reset it
        $pw = New-RandomPassword
        Invoke-Graph PATCH "/users/$($u.id)" @{ passwordProfile = @{ password = $pw; forceChangePasswordNextSignIn = $false } } | Out-Null
    }
    Set-EnvValue "PORTOPS_TESTUSER_${T}_UPN" $upn
    Set-EnvValue "PORTOPS_TESTUSER_${T}_PASSWORD" $pw
    Add-GroupMember "sg-portops-team-$team" $u.id
}
# the signed-in admin: Ops team + power tier (for the "me" demo)
Add-GroupMember 'sg-portops-team-ops' $me.id
Add-GroupMember 'sg-portops-power-users' $me.id

# ---------------------------------------------------------------- public client app for demos
$cli = Find-One '/applications' "displayName eq '$ClientAppName'"
if (-not $cli) {
    Write-Host "Creating app registration $ClientAppName"
    $cli = Invoke-Graph POST '/applications' @{ displayName = $ClientAppName; signInAudience = 'AzureADMyOrg' }
}
Invoke-Graph PATCH "/applications/$($cli.id)" @{
    isFallbackPublicClient = $true
    publicClient = @{ redirectUris = @('http://localhost') }
    requiredResourceAccess = @(@{ resourceAppId = $apiAppId; resourceAccess = @(@{ id = $scopeId; type = 'Scope' }) })
} | Out-Null
$clientAppId = $cli.appId
# pre-authorize the demo client for the scope as well
$preAuth = @(
    @{ appId = $azureCliAppId; delegatedPermissionIds = @($scopeId) }
    @{ appId = $clientAppId;   delegatedPermissionIds = @($scopeId) }
)
Invoke-Graph PATCH "/applications/$appObjId" @{ api = @{ preAuthorizedApplications = $preAuth } } | Out-Null

$csp = Find-One '/servicePrincipals' "appId eq '$clientAppId'"
if (-not $csp) { $csp = Invoke-GraphRetry POST '/servicePrincipals' @{ appId = $clientAppId } }
$grant = (Invoke-Graph GET "/oauth2PermissionGrants?`$filter=clientId eq '$($csp.id)' and resourceId eq '$($sp.id)'" $null).value | Select-Object -First 1
if (-not $grant) {
    Invoke-GraphRetry POST '/oauth2PermissionGrants' @{ clientId = $csp.id; consentType = 'AllPrincipals'; resourceId = $sp.id; scope = 'Gateway.Use' } | Out-Null
    Write-Host 'Admin consent granted for Gateway.Use'
}
Write-Host "Client app: $clientAppId"

# ---------------------------------------------------------------- .env
Set-EnvValue 'ENTRA_TENANT_ID' $tenantId
Set-EnvValue 'ENTRA_API_APP_ID' $apiAppId
Set-EnvValue 'ENTRA_API_AUDIENCE' "api://$ApiAppName"
Set-EnvValue 'ENTRA_CLIENT_APP_ID' $clientAppId
Write-Host "Done. Wrote ENTRA_* and PORTOPS_TESTUSER_* to $envFile (git-ignored). Next: .\scripts\deploy.ps1"
