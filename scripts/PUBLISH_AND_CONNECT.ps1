# One-command deploy and Windows connection; no hidden secrets in GitHub or CLI args.
param([string]$ApiUrl = 'https://talos-unified-v2.onrender.com')
$ErrorActionPreference='Stop'
$publish=Join-Path $PSScriptRoot 'PUBLISH_REPLACE_GITHUB.ps1'
& $publish
if($LASTEXITCODE -ne 0){throw 'GitHub publish did not complete successfully'}
Write-Host 'Waiting for Render automatic deployment of API 2.6.0...' -ForegroundColor Cyan
$ready=$false
for($i=0;$i -lt 45;$i++){
    Start-Sleep -Seconds 10
    try{$h=Invoke-RestMethod -Method Get -Uri "$($ApiUrl.TrimEnd('/'))/health" -TimeoutSec 12
        if($h.version -eq '2.6.0'){$ready=$true;break}
    }catch{Write-Host 'Render deploying / cold starting...'}
}
if(!$ready){throw 'Render v2.6 not LIVE yet. Check Render Deploys then run scripts\CONNECT_ALL_REMOTE.ps1 later.'}
Write-Host 'Render Talos v2.6 confirmed LIVE' -ForegroundColor Green
& (Join-Path $PSScriptRoot 'CONNECT_ALL_REMOTE.ps1') -ApiUrl $ApiUrl
