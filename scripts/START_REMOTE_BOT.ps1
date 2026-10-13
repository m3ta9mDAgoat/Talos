param([Parameter(Mandatory=$true)][string]$ApiUrl)
$ErrorActionPreference='Stop'
if($ApiUrl -notmatch '^https://'){throw 'Remote API URL must use HTTPS'}
$root=Split-Path -Parent $PSScriptRoot
$py=Join-Path $root '.venv\Scripts\python.exe'
if(!(Test-Path $py)){
    $python=Get-Command python -ErrorAction SilentlyContinue
    if(!$python){throw 'Python 3.12+ is required. Install Python for Windows and rerun.'}
    & python -m venv (Join-Path $root '.venv')
    if($LASTEXITCODE -ne 0){throw 'Cannot create Python virtualenv'}
    & $py -m pip install -r (Join-Path $root 'requirements.txt')
    if($LASTEXITCODE -ne 0){throw 'Cannot install bot dependencies'}
}

$root=Split-Path -Parent $PSScriptRoot
if(-not [Environment]::GetEnvironmentVariable('TALOS_LAB_ADMIN_TOKEN','Process')){
$secret=Read-Host 'Enter TALOS_LAB_ADMIN_TOKEN from Render (never share it in chat)' -AsSecureString
$ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try{$env:TALOS_LAB_ADMIN_TOKEN=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)}finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)}
}
& (Join-Path $root 'scripts\START_BOT_CONNECTED.ps1') -ApiUrl $ApiUrl
