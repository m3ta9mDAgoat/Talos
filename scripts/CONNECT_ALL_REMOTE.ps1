# Talos v2.6: one entrypoint for existing user-approved BotFather bot + Windows worker.
# Secrets live only in this PowerShell process and its immediate child processes.
param([string]$ApiUrl = 'https://talos-unified-v2.onrender.com')
$ErrorActionPreference='Stop'
if($ApiUrl -notmatch '^https://[A-Za-z0-9.-]+/?$'){throw 'HTTPS public Render URL required'}
$ApiUrl=$ApiUrl.TrimEnd('/')
$root=Split-Path -Parent $PSScriptRoot
$existing='C:\TalosEngines\TalosBotV2\bot_config.json'
$local=Join-Path $root 'bot_config.json'
if(!(Test-Path $existing) -and !(Test-Path $local)){
    throw 'Existing Telegram bot config not found. Restore it locally to C:\TalosEngines\TalosBotV2\bot_config.json. Never upload token.'
}
$bc=if(Test-Path $local){Get-Content -Raw $local|ConvertFrom-Json}else{Get-Content -Raw $existing|ConvertFrom-Json}
if(!$bc.bot_token -or $bc.bot_token -like 'PASTE_*'){throw 'The BotFather token is missing in your LOCAL bot_config.json'}
if(@($bc.owner_user_ids).Count -eq 0){throw 'Add YOUR numeric Telegram /id to owner_user_ids in bot_config.json locally before starting.'}
Write-Host 'Validating Talos hosted Research API...' -ForegroundColor Cyan
$health=Invoke-RestMethod -Method Get -Uri "$ApiUrl/health" -TimeoutSec 45
if($health.version -ne '2.6.0'){
    throw "Hosted API is $($health.version), expected 2.6.0. Publish Talos v2.6 and wait until its Render deploy is LIVE."
}
$py=Join-Path $root '.venv\Scripts\python.exe'
if(!(Test-Path $py)){
    if(!(Get-Command python -ErrorAction SilentlyContinue)){throw 'Python 3.12+ required on Windows'}
    & python -m venv (Join-Path $root '.venv')
    if($LASTEXITCODE -ne 0){throw 'Python virtual environment creation failed'}
    & $py -m pip install -r (Join-Path $root 'requirements.txt')
    if($LASTEXITCODE -ne 0){throw 'Dependency installation failed'}
}
# Avoid plain CLI arguments, temp files, chat messages, git files and logs.
function Get-SecretValue([string]$Key,[string]$Title){
    $val=[Environment]::GetEnvironmentVariable($Key,'Process')
    if($val){return $val}
    $secure=Read-Host $Title -AsSecureString
    $ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try{return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)}
    finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)}
}
$admin=Get-SecretValue 'TALOS_LAB_ADMIN_TOKEN' 'Enter ADMIN token from Render Environment'
$worker=Get-SecretValue 'TALOS_LAB_WORKER_TOKEN' 'Enter WORKER token from Render Environment'
if($admin.Length -lt 32 -or $worker.Length -lt 32 -or $admin -eq $worker){throw 'Invalid credentials: use two distinct >=32-character secrets'}
$headers=@{Authorization='Bearer '+$admin}
try{$status=Invoke-RestMethod -Uri "$ApiUrl/api/v2/integration/status" -Headers $headers -Method Get -TimeoutSec 45}
catch{throw 'Admin authentication to hosted API failed. Check TALOS_LAB_ADMIN_TOKEN in Render.'}
Write-Host 'Authenticated hosted API. Telegram and Windows processes will run independently.' -ForegroundColor Green
Write-Host 'IMPORTANT: Close the OLD Telegram bot polling process first (only one poller per bot token).' -ForegroundColor Yellow
$confirm=Read-Host 'Have you closed the old bot console? Type STOPPED'
if($confirm -cne 'STOPPED'){throw 'Stopped to avoid duplicate Telegram getUpdates pollers.'}
$scriptBot=Join-Path $root 'scripts\START_REMOTE_BOT.ps1'
$scriptWorker=Join-Path $root 'scripts\START_REMOTE_WORKER.ps1'
$env:TALOS_LAB_ADMIN_TOKEN=$admin
Remove-Item Env:TALOS_LAB_WORKER_TOKEN -ErrorAction SilentlyContinue
$argBot='-NoProfile -ExecutionPolicy Bypass -File "'+$scriptBot+'" -ApiUrl "'+$ApiUrl+'"'
Start-Process -FilePath powershell.exe -ArgumentList $argBot -WorkingDirectory $root
Remove-Item Env:TALOS_LAB_ADMIN_TOKEN -ErrorAction SilentlyContinue
$env:TALOS_LAB_WORKER_TOKEN=$worker
$argWorker='-NoProfile -ExecutionPolicy Bypass -File "'+$scriptWorker+'" -ApiUrl "'+$ApiUrl+'"'
Start-Process -FilePath powershell.exe -ArgumentList $argWorker -WorkingDirectory $root
Remove-Item Env:TALOS_LAB_WORKER_TOKEN -ErrorAction SilentlyContinue
$admin=$null;$worker=$null
Write-Host 'Both windows launched. Waiting for authenticated service heartbeats (up to 90 seconds)...' -ForegroundColor Cyan
# No secrets are persisted; read-only status poll is authenticated via a process-local header.
$success=$false
for($i=0;$i -lt 18;$i++){
    Start-Sleep -Seconds 5
    try{
        $s=Invoke-RestMethod -Uri "$ApiUrl/api/v2/integration/status" -Headers $headers -TimeoutSec 25
        $b=$s.bot.status;$w=$s.windows_worker.status;$h=$s.hosted_reference_worker.status
        Write-Host "Telegram: $b | Windows Worker: $w | Hosted Reference Worker: $h"
        if($b -eq 'online' -and $w -eq 'online'){$success=$true;break}
    }catch{Write-Host 'API status temporarily unavailable; retrying...'}
}
if($success){Write-Host 'CONNECTED: Telegram + Windows Worker + Hosted API/DB' -ForegroundColor Green}
else{Write-Warning 'One or more remote agents have not sent a heartbeat yet. Check BOTH new PowerShell windows for errors; do not claim they are connected.'}
Write-Host 'Talos UI:' $ApiUrl
Write-Host 'LIVE ORDERS DISABLED. Stage3 native verification is limited to the installed parity fixture.'
