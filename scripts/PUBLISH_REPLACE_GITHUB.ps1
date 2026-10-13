# Talos Unified v2.2: replace old repository sources (Git history remains available).
# Run only from a CLEAN extracted Talos Unified v2.2 delivery, never from a working dir holding secrets.
param([string]$Repository = 'https://github.com/m3ta9mDAgoat/Talos.git')
$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $PSScriptRoot
$required = @('Dockerfile','requirements.txt','server\api.py','server\deployment.py','core\backtest.py','web\index.html','web\APPROVED_ORIGINAL.html','worker\agent.py','talos_bot\runner.py')
foreach ($p in $required) {
    if (!(Test-Path -LiteralPath (Join-Path $source $p) -PathType Leaf)) { throw "Unified v2.2 source incomplete. Missing $p" }
}
$git = Get-Command git -ErrorAction SilentlyContinue
if (!$git) { throw 'Git for Windows is required. Install Git and authenticate with GitHub before running this script.' }
$forbidden = @('bot_config.json','.local_auth.json','.env','.bot_runtime.sqlite3')
foreach ($item in $forbidden) {
    if (Test-Path -LiteralPath (Join-Path $source $item)) {
        throw "Refusing to publish a folder containing secret/runtime data: $item. Extract a clean copy of the provided ZIP instead."
    }
}
$confirm = Read-Host 'Replace all tracked files of m3ta9mDAgoat/Talos main with Talos Unified v2.2? Type REPLACE'
if ($confirm -cne 'REPLACE') { Write-Host 'Cancelled. No changes made.'; exit 0 }
$root = Join-Path ([IO.Path]::GetTempPath()) ('talos-v22-deploy-' + [guid]::NewGuid().ToString('N'))
$clone = Join-Path $root 'repo'
New-Item -ItemType Directory -Path $root -Force | Out-Null
try {
    & git clone --depth 1 --branch main -- $Repository $clone
    if ($LASTEXITCODE -ne 0) { throw 'Git clone failed. Authenticate with your GitHub account.' }
    Push-Location $clone
    try {
        & git rm -r -q -- .
        if ($LASTEXITCODE -ne 0) { throw 'Unable to stage removal of old tracked source files.' }
        $items = Get-ChildItem -LiteralPath $source -Force | Where-Object { $_.Name -notin @('.git','.venv','__pycache__','.pytest_cache') }
        foreach ($item in $items) { Copy-Item -LiteralPath $item.FullName -Destination $clone -Recurse -Force }
        & git add -A
        if ($LASTEXITCODE -ne 0) { throw 'Git add failed.' }
        $count = @(Get-ChildItem -LiteralPath $source -File -Recurse | Where-Object { $_.FullName -notmatch '\\(\.venv|__pycache__|\.pytest_cache)\\' }).Count
        if ($count -ge 100) { throw "Source package has $count files. Must be fewer than 100." }
        $pending = & git status --porcelain
        if (!$pending) { Write-Host 'GitHub is already at the same source snapshot.'; exit 0 }
        & git -c user.name='Talos Deployment' -c user.email='talos-deploy@users.noreply.github.com' commit -m 'Deploy Talos Unified v2.2 (replace legacy main)'
        if ($LASTEXITCODE -ne 0) { throw 'Git commit failed.' }
        & git push origin HEAD:main
        if ($LASTEXITCODE -ne 0) { throw 'GitHub push failed. Old remote main was not replaced.' }
        Write-Host 'SUCCESS: Talos Unified v2.2 pushed to main. Render automatic deploy will start.' -ForegroundColor Green
        Write-Host 'The existing Render service still needs hosted PostgreSQL and bearer-token environment variables.' -ForegroundColor Yellow
    } finally { Pop-Location }
} finally {
    if (Test-Path $root) { Remove-Item -LiteralPath $root -Force -Recurse -ErrorAction SilentlyContinue }
}
