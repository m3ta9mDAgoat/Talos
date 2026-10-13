param([Parameter(Mandatory=$true)][string]$ApiUrl)
$ErrorActionPreference='Stop'
if($ApiUrl -notmatch '^https://'){throw 'Remote API URL must use HTTPS'}
$root=Split-Path -Parent $PSScriptRoot
$py=Join-Path $root '.venv\Scripts\python.exe'
if(!(Test-Path $py)){
    $python=Get-Command python -ErrorAction SilentlyContinue
    if(!$python){throw 'Python 3.12+ required for the Windows worker'}
    & python -m venv (Join-Path $root '.venv')
    if($LASTEXITCODE -ne 0){throw 'Cannot create Python virtualenv'}
    & $py -m pip install -r (Join-Path $root 'requirements.txt')
    if($LASTEXITCODE -ne 0){throw 'Cannot install Worker dependencies'}
}
if(-not [Environment]::GetEnvironmentVariable('TALOS_LAB_WORKER_TOKEN','Process')){
$secret=Read-Host 'Enter TALOS_LAB_WORKER_TOKEN from your Render environment' -AsSecureString
$ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try{$env:TALOS_LAB_WORKER_TOKEN=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)}finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)}
}
$env:TALOS_LAB_WORKER_MODE='windows_remote'
$env:TALOS_LAB_URL=$ApiUrl
$env:TALOS_STAGE3_ROOT='C:\TalosEngines\TalosStage3'
Set-Location $root
Write-Host 'Starting LOCAL Windows worker against HTTPS server; LIVE ORDERS DISABLED.'
& $py -m worker.agent
