$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
$py=Join-Path $root '.venv\Scripts\python.exe'
if(!(Test-Path $py)){throw 'First run START_LOCAL.ps1'}
$auth=Get-Content -LiteralPath (Join-Path $root '.local_auth.json') -Raw | ConvertFrom-Json
$env:TALOS_LAB_WORKER_TOKEN=$auth.worker
$env:TALOS_LAB_URL='http://127.0.0.1:8018'
$env:TALOS_STAGE3_ROOT='C:\TalosEngines\TalosStage3'
Write-Host 'Talos research worker · local jobs only. LIVE ORDERS DISABLED.'
& $py -m worker.agent
