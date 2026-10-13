$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
if(!(Test-Path '.\.venv\Scripts\python.exe')) { python -m venv .venv }
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt
if($LASTEXITCODE -ne 0) {throw 'Requirements failed'}
if(!$env:TALOS_LAB_ADMIN_TOKEN -or !$env:TALOS_LAB_WORKER_TOKEN){throw 'Set TALOS_LAB_ADMIN_TOKEN and TALOS_LAB_WORKER_TOKEN first'}
& '.\.venv\Scripts\python.exe' -m uvicorn server.api:app --host 127.0.0.1 --port 8018
