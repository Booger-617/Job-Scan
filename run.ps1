# Daily entrypoint. Called by Task Scheduler, or run it by hand.
#   .\run.ps1              full run (fetch -> rules -> Claude -> HTML)
#   .\run.ps1 -NoLlm       rules only, zero API cost
#   .\run.ps1 -Quiet       no browser popup (what the scheduled task uses)

param(
    [switch]$NoLlm,
    [switch]$Quiet,
    [switch]$Verify
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

# Put your key here (or set it once, user-wide, with:
#   [Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY","sk-ant-...","User")  )
if (-not $env:ANTHROPIC_API_KEY) {
    $keyFile = Join-Path $here ".env"
    if (Test-Path $keyFile) {
        Get-Content $keyFile | ForEach-Object {
            if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.+?)\s*$') {
                [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
            }
        }
    }
}

$logDir = Join-Path $here "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$logFile = Join-Path $logDir ("run-" + (Get-Date -Format "yyyy-MM-dd") + ".log")

if ($Verify) {
    python -m jobscan verify
    exit $LASTEXITCODE
}

$argsList = @("-m", "jobscan", "run")
if ($NoLlm) { $argsList += "--no-llm" }
if ($Quiet) { $argsList += "--no-open" }

"=== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') ===" | Out-File -FilePath $logFile -Append -Encoding utf8

# Tee-Object -FilePath writes UTF-16 on PowerShell 5.1, which makes the log
# unreadable to every normal tool. Capture to a variable, then write UTF-8.
python @argsList | Tee-Object -Variable runOutput
$code = $LASTEXITCODE
$runOutput | Out-File -FilePath $logFile -Append -Encoding utf8

if ($code -ne 0) {
    "run failed with exit code $code" | Out-File -FilePath $logFile -Append -Encoding utf8
}
exit $code
