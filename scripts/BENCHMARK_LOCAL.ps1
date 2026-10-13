$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$py=Join-Path $root '.venv\Scripts\python.exe'
if(!(Test-Path $py)){throw 'Run START_LOCAL.ps1 first to create the local venv'}
& $py (Join-Path $root 'scripts\benchmark_local.py')
if($LASTEXITCODE -ne 0){throw 'Offline benchmark failed'}
