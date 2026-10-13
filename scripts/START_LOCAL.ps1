$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
$py=Join-Path $root '.venv\Scripts\python.exe'
if(!(Test-Path $py)) {
    Write-Host 'Creating isolated Talos Research venv (will not change existing TalosBridge/LEAN).'
    python -m venv (Join-Path $root '.venv')
    if($LASTEXITCODE -ne 0){throw 'Python venv creation failed'}
}
& $py -m pip install -r (Join-Path $root 'requirements.txt')
if($LASTEXITCODE -ne 0){throw 'Dependency installation failed'}
& $py (Join-Path $root 'scripts\local_setup.py')
if($LASTEXITCODE -ne 0){throw 'Local secret initialization failed'}
Write-Host 'Launching 2 local PowerShell windows: Research API + Research Worker...'
$server=Join-Path $root 'scripts\START_SERVER.ps1'
$worker=Join-Path $root 'scripts\START_WORKER.ps1'
Start-Process -FilePath powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$server+'"'))
Start-Sleep -Seconds 2
Start-Process -FilePath powershell.exe -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$worker+'"'))
Write-Host 'Open http://127.0.0.1:8018/lab after the server reports startup. Never expose port 8018 to the internet.'
