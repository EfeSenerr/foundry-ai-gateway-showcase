<#
 Refreshes Azure list prices in infra/pricing/model_prices.json and pushes them into the deployed spend alerts
 (budget kill switch) and the workbook. Models marked "source": "manual" keep their (negotiated) prices.

 Usage:
   .\scripts\refresh-prices.ps1                 # run now
   .\scripts\refresh-prices.ps1 -DryRun         # show changes only
   .\scripts\refresh-prices.ps1 -Register       # weekly Windows scheduled task (Monday 06:00)
   .\scripts\refresh-prices.ps1 -Unregister
 The task runs as the current user and needs a valid `az login`. For an always-on setup run the same script from
 an Azure Automation runbook or a pipeline with a managed identity instead.
#>
param([switch]$DryRun, [switch]$Register, [switch]$Unregister)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'demo\.venv\Scripts\python.exe'
$task = 'portops-refresh-model-prices'

if ($Unregister) { Unregister-ScheduledTask -TaskName $task -Confirm:$false; Write-Host "Removed task $task"; return }
if ($Register) {
    $pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue)?.Source ?? (Get-Command powershell).Source
    $action = New-ScheduledTaskAction -Execute $pwsh -Argument "-NoProfile -File `"$PSCommandPath`"" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 6am
    Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger -Description 'Refresh model list prices (portops PoC)' -Force | Out-Null
    Write-Host "Registered weekly task '$task' (Monday 06:00). Log: $root\infra\pricing\refresh.log"
    return
}

$log = Join-Path $root 'infra\pricing\refresh.log'
$argsList = @((Join-Path $root 'demo\refresh_prices.py'))
if ($DryRun) { $argsList += '--dry-run' }
"=== $(Get-Date -Format s)" | Add-Content $log
& $py @argsList 2>&1 | Tee-Object -FilePath $log -Append
exit $LASTEXITCODE
