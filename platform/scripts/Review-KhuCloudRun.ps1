$ErrorActionPreference='Stop'
$TaskNode=(Get-Command node -ErrorAction SilentlyContinue).Source
if(-not $TaskNode){$TaskNode=Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'}
Write-Output 'Read-only price proposal. This command creates no VM and changes no lab server.'
& $TaskNode (Join-Path $PSScriptRoot 'review-cloud-plan.mjs')
if($LASTEXITCODE -ne 0){throw 'Current official price review was unavailable; no cloud action was performed.'}
