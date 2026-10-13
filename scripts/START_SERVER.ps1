$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
$py=Join-Path $root '.venv\Scripts\python.exe'
if(!(Test-Path $py)){throw 'First run START_LOCAL.ps1 to prepare the local virtualenv'}
$auth=Get-Content -LiteralPath (Join-Path $root '.local_auth.json') -Raw | ConvertFrom-Json
$env:TALOS_LAB_ADMIN_TOKEN=$auth.admin
$env:TALOS_LAB_WORKER_TOKEN=$auth.worker
$env:TALOS_LAB_DATABASE_URL='sqlite:///'+(($root -replace '\\','/')+'/talos_lab.sqlite3')
Write-Host 'Talos Research API on http://127.0.0.1:8018/lab (LOCAL ONLY)'
& $py -m uvicorn server.api:app --host 127.0.0.1 --port 8018
