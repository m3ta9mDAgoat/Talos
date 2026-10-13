param([Parameter(Mandatory=$true)][string]$ApiUrl)
$ErrorActionPreference='Stop'
if($ApiUrl -notmatch '^https://'){throw 'Remote API URL must use HTTPS'}
$root=Split-Path -Parent $PSScriptRoot
$secret=Read-Host 'Enter TALOS_LAB_ADMIN_TOKEN from Render (never share it in chat)' -AsSecureString
$ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try{$env:TALOS_LAB_ADMIN_TOKEN=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)}finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)}
& (Join-Path $root 'scripts\START_BOT_CONNECTED.ps1') -ApiUrl $ApiUrl
