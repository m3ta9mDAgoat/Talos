# Run in PowerShell from the extracted Talos v4.4.1 folder.
# Safeguards: stops if existing repo has uncommitted changes; does not touch .git or .env.
param(
  [Parameter(Mandatory = $true)] [string]$RepoPath,
  [switch]$CommitAndPush
)
$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = (Resolve-Path $RepoPath).Path
if (-not (Test-Path (Join-Path $repo '.git'))) { throw "Not a Git repository: $repo" }
$existing = @(git -C $repo status --porcelain)
if ($LASTEXITCODE -ne 0 -or $existing.Count -ne 0) {
  throw 'Existing Talos checkout is not clean. Commit or stash your changes before upgrading.'
}
Write-Host 'Copying Talos v4.4.1 to existing Git checkout (excluding .env, .git and build/cache folders)...'
$excludeDirs = @('.git','node_modules','__pycache__','.pytest_cache','dist')
$excludeFiles = @('.env','.env.production')
& robocopy $source $repo /E /XD $excludeDirs /XF $excludeFiles /NFL /NDL /NJH /NJS /NP | Out-Host
if ($LASTEXITCODE -gt 7) { throw "File copy failed with Robocopy code $LASTEXITCODE" }
Write-Host "\nDiff summary:"
& git -C $repo diff --stat
if ($CommitAndPush) {
  & git -C $repo add --all
  if ($LASTEXITCODE -ne 0) { throw 'Git add failed' }
  & git -C $repo commit -m 'Talos v4.4.1: multi-asset OSINT and mission control'
  if ($LASTEXITCODE -ne 0) { throw 'Git commit failed: check user.name and user.email' }
  & git -C $repo push
  if ($LASTEXITCODE -ne 0) { throw 'Git push failed; inspect GitHub authentication and remote' }
  Write-Host 'Pushed to the existing GitHub branch. Inspect Render deployment logs.'
} else {
  Write-Host "\nFiles copied. Review changes, then run:"
  Write-Host "  git -C '$repo' add --all"
  Write-Host "  git -C '$repo' commit -m 'Talos v4.4.1'"
  Write-Host "  git -C '$repo' push"
}
