# Registers (or removes) a Windows Task Scheduler job that runs jobscan daily.
#
#   .\register_task.ps1                 -> every day at 07:30
#   .\register_task.ps1 -Time 08:00     -> custom time
#   .\register_task.ps1 -Remove         -> unregister
#
# Runs under your own account, only while you are logged on, and will fire on the
# next logon if the machine was asleep at the scheduled time.

param(
    [string]$Time = "07:30",
    [string]$TaskName = "Jobscan Daily",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$runner = Join-Path $here "run.ps1"

if ($Remove) {
    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
        Write-Host "removed scheduled task '$TaskName'"
    } catch {
        Write-Host "no task named '$TaskName' found"
    }
    exit 0
}

if (-not (Test-Path $runner)) { throw "run.ps1 not found at $runner" }

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -Quiet" `
    -WorkingDirectory $here

$trigger = New-ScheduledTaskTrigger -Daily -At $Time

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10)

try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop } catch {}

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Daily job matching scan" | Out-Null

Write-Host "registered '$TaskName' - runs daily at $Time"
Write-Host "reports land in: $(Join-Path $here 'reports')"
Write-Host "run it once now with:  Start-ScheduledTask -TaskName '$TaskName'"
