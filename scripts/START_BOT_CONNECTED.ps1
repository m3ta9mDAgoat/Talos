param(
    [string]$ApiUrl = 'http://127.0.0.1:8018'
)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$py=Join-Path $root '.venv\Scripts\python.exe'
if(!(Test-Path $py)){throw 'Run scripts\START_LOCAL.ps1 first to create the venv'}
$authPath=Join-Path $root '.local_auth.json'
if(!(Test-Path $authPath) -and $ApiUrl -like 'http://127.0.0.1:*'){throw 'Missing local API auth file'}
if($ApiUrl -like 'http://127.0.0.1:*'){
    $auth=Get-Content -LiteralPath $authPath -Raw | ConvertFrom-Json
    $env:TALOS_LAB_ADMIN_TOKEN=$auth.admin
}
$existing='C:\TalosEngines\TalosBotV2\bot_config.json'
if(Test-Path (Join-Path $root 'bot_config.json')){
    $env:TALOS_BOT_CONFIG=Join-Path $root 'bot_config.json'
} elseif(Test-Path $existing){
    $env:TALOS_BOT_CONFIG=$existing
} else {
    Copy-Item (Join-Path $root 'bot_config.example.json') (Join-Path $root 'bot_config.json')
    Write-Host 'Bot config created. Put your existing BotFather token and owner_user_ids into bot_config.json (local only).'
    exit 0
}
$env:TALOS_BOT_DB=Join-Path (Split-Path -Parent $env:TALOS_BOT_CONFIG) '.bot_runtime.sqlite3'
$env:TALOS_BOT_TALOS_URL=$ApiUrl
$env:TALOS_BOT_RESEARCH_ROOT=$root
Set-Location $root
Write-Host 'Starting Talos Telegram bot against' $ApiUrl 'NO LIVE ORDERS. Do not run the old bot process simultaneously.'
& $py -m talos_bot.runner
if($LASTEXITCODE -ne 0){throw "Telegram bot ended with code $LASTEXITCODE"}
