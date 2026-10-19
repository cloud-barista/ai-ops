param([string]$Node=(Get-Command node -ErrorAction Stop).Source)
$ErrorActionPreference='Stop'
# This process is independent of the UI process and retains pending cleanup.
& $Node (Join-Path (Split-Path $PSScriptRoot -Parent) 'server/cloud-watcher.mjs')
if($LASTEXITCODE -ne 0){throw 'Cloud cleanup watcher stopped. Live creation stays blocked.'}
