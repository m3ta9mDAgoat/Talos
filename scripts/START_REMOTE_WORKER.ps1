param([Parameter(Mandatory=$true)][string]$ApiUrl)
$ErrorActionPreference='Stop'
if($ApiUrl -notmatch '^https://'){throw 'Remote API URL must use HTTPS'}
$root=Split-Path -Parent $PSScriptRoot
$py=Join-Path $root '.venv\Scripts\python.exe'
if(!(Test-Path $py)){throw 'Run START_LOCAL once to prepare Python dependencies'}
$secret=Read-Host 'Enter TALOS_LAB_WORKER_TOKEN from your Render environment' -AsSecureString
$ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try{$env:TALOS_LAB_WORKER_TOKEN=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)}finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)}
$env:TALOS_LAB_URL=$ApiUrl
$env:TALOS_STAGE3_ROOT='C:\TalosEngines\TalosStage3'
Set-Location $root
Write-Host 'Starting LOCAL Windows worker against HTTPS server; LIVE ORDERS DISABLED.'
& $py -m worker.agent
