<#
 Applies openwebui\catalog.json to a running Open WebUI: creates the Team.* groups (if not yet synced) and one model
 entry per raw model / agent / workspace model with read access for the listed groups. Idempotent (create or update).
 Token: optional. Without -Token the script mints a 30-min admin token inside the local container (portops-openwebui)
 with Open WebUI's own create_token (never printed). Otherwise pass an admin API key (Settings > Account > API keys).
 Usage: .\scripts\setup-openwebui-catalog.ps1 [-Token <admin token>] [-BaseUrl http://localhost:3000]
#>
[CmdletBinding()]
param(
    [string]$Token,
    [string]$BaseUrl = 'http://localhost:3000',
    [string]$Container = 'portops-openwebui',
    [string]$Catalog = (Join-Path $PSScriptRoot '..\openwebui\catalog.json')
)
$ErrorActionPreference = 'Stop'
if (-not $Token) {
    $py = 'import sqlite3,datetime; from open_webui.utils.auth import create_token; c=sqlite3.connect("/app/backend/data/webui.db"); uid=c.execute("select id from user where role=''admin'' order by created_at limit 1").fetchone()[0]; st=c.execute("select session_stamp from auth where id=?",(uid,)).fetchone()[0]; print(create_token(data={"id":uid,"session_stamp":st,"auth_method":"oauth"}, expires_delta=datetime.timedelta(minutes=30)))'
    $Token = (docker exec $Container python -c $py 2>$null | Select-Object -Last 1)
    if (-not $Token -or $Token.Trim().Length -lt 50) { throw "Could not mint an admin token in container $Container (sign in once as admin first)." }
    $Token = $Token.Trim()
}
$h = @{ Authorization = "Bearer $Token" }
function Api([string]$Method, [string]$Path, $Body) {
    $p = @{ Method = $Method; Uri = "$BaseUrl/api/v1$Path"; Headers = $h }
    if ($null -ne $Body) { $p.ContentType = 'application/json'; $p.Body = ($Body | ConvertTo-Json -Depth 20) }
    Invoke-RestMethod @p
}
$cat = Get-Content -Raw $Catalog | ConvertFrom-Json

$groups = @{}
foreach ($g in (Api GET '/groups/' $null)) { $groups[$g.name] = $g.id }
foreach ($g in $cat.groups) {
    if (-not $groups.ContainsKey($g.name)) {
        $new = Api POST '/groups/create' @{ name = $g.name; description = $g.description }
        $groups[$g.name] = $new.id; Write-Host "Group $($g.name) created"
    }
}

$existing = @{}
foreach ($m in (Api GET '/models/all' $null)) { $existing[$m.id] = $true }
foreach ($m in $cat.models) {
    $params = @{}
    if ($m.system) { $params.system = $m.system }
    $form = @{
        id = $m.id; base_model_id = $m.base_model_id; name = $m.name; is_active = $true
        meta = @{ description = $m.description; tags = @($m.tags | ForEach-Object { @{ name = $_ } }); capabilities = @{} }
        params = $params
        access_grants = @($m.groups | ForEach-Object { @{ principal_type = 'group'; principal_id = $groups[$_]; permission = 'read' } })
    }
    if ($existing.ContainsKey($m.id)) { Api POST '/models/model/update' $form | Out-Null; $verb = 'updated' }
    else { Api POST '/models/create' $form | Out-Null; $verb = 'created' }
    Write-Host ("{0,-24} {1,-8} -> {2}" -f $m.id, $verb, ($m.groups -join ', '))
}
