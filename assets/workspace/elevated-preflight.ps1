$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $root 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$transcript = Join-Path $logDir 'elevated-preflight-transcript.txt'
$python = Join-Path $root '.venv\Scripts\python.exe'
$service = Join-Path $root 'service.py'
Start-Transcript -Path $transcript -Force | Out-Null
& $python $service preflight
$exitCode = $LASTEXITCODE
Stop-Transcript | Out-Null
exit $exitCode
