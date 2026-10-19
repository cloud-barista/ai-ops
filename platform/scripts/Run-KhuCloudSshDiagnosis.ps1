param([ValidateSet('Review','Check','Execute')][string]$Mode='Review',[switch]$ApproveCost,[double]$ComputeBudgetUsd=0)
$ErrorActionPreference='Stop'
$TaskWorkspace=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$TaskProcessPath=Join-Path $TaskWorkspace 'output\khu-platform-validation\runtime\processes.json'
$TaskCandidates=@()
$TaskCommand=Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if($TaskCommand){$TaskCandidates+=@($TaskCommand.Source)}
if(Test-Path -LiteralPath $TaskProcessPath){$TaskCandidates+=@(Get-Content -LiteralPath $TaskProcessPath -Raw | ConvertFrom-Json | Where-Object {$_.name -eq 'platform'} | ForEach-Object {$_.executable})}
$TaskCandidates+=@((Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'),(Join-Path $env:ProgramFiles 'nodejs\node.exe'),(Join-Path $env:LOCALAPPDATA 'Programs\nodejs\node.exe'))
$TaskNode=$TaskCandidates | Where-Object {$_ -and [IO.Path]::IsPathRooted($_) -and [IO.Path]::GetFileName($_) -ieq 'node.exe' -and (Test-Path -LiteralPath $_ -PathType Leaf)} | Select-Object -First 1
if(-not $TaskNode){throw 'Existing PoC Node runtime is unavailable. No VM was created.'}
$TaskScript=Join-Path $PSScriptRoot 'diagnose-cloud-ssh.mjs'
if($Mode -eq 'Check'){
    Write-Output 'Read-only preflight check. No VM creation, cost approval, provider cleanup, or lab file changes are requested.'
    & $TaskNode $TaskScript --check-only
    exit $LASTEXITCODE
}
if($Mode -eq 'Review'){
    Write-Output 'Cost review only. No cloud VM is created.'
    & $TaskNode $TaskScript
    exit $LASTEXITCODE
}
if(-not $ApproveCost -or $ComputeBudgetUsd -le 0){throw 'Execute requires -ApproveCost and a reviewed positive -ComputeBudgetUsd. No VM was created.'}
Write-Output 'Approved paid diagnosis: one CPU VM, at most two fixed read-only SSH probes, then exact VM and attachment cleanup. No model installation or inference.'
$TaskBudget=$ComputeBudgetUsd.ToString('R',[Globalization.CultureInfo]::InvariantCulture)
& $TaskNode $TaskScript --execute-approved --compute-budget-usd $TaskBudget
exit $LASTEXITCODE
