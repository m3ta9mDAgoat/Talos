$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host 'Preparing Talos Unified locally. No GitHub/Render/Neon changes.'
& (Join-Path $root 'scripts\START_LOCAL.ps1')
if($LASTEXITCODE -and $LASTEXITCODE -ne 0){throw 'Local starter failed'}
$launcher=Join-Path $root 'scripts\START_BOT_CONNECTED.ps1'
$existing='C:\TalosEngines\TalosBotV2\bot_config.json'
if((Test-Path (Join-Path $root 'bot_config.json')) -or (Test-Path $existing)) {
    Write-Host 'Launching Telegram bot using the EXISTING configuration. Stop any old process polling the same bot before launching.' -ForegroundColor Yellow
    # Allow START_LOCAL time to start API before bot connects.
    Start-Sleep -Seconds 3
    Start-Process -FilePath powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-NoExit','-File', ('"'+$launcher+'"'))
} else {
    Write-Host 'Telegram config unavailable. Research Plane started; run START_BOT_CONNECTED.ps1 to create configuration.' -ForegroundColor Yellow
}
Write-Host 'Talos Lab: http://127.0.0.1:8018/lab. Trade execution disabled.'
