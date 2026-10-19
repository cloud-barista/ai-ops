$ErrorActionPreference='Stop'
$TaskLauncher=Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\Run-KhuResearchPoc.ps1'
$TaskTokens=$null;$TaskErrors=$null
$TaskAst=[System.Management.Automation.Language.Parser]::ParseFile($TaskLauncher,[ref]$TaskTokens,[ref]$TaskErrors)
if($TaskErrors.Count){throw 'Launcher has PowerShell syntax errors'}
$TaskOwnershipFunction=$TaskAst.Find({param($Node) $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $Node.Name -eq 'Own-Process'},$true)
if(-not $TaskOwnershipFunction){throw 'Process ownership function missing'}
# Load only the ownership function. No services are started or stopped by this test.
. ([scriptblock]::Create($TaskOwnershipFunction.Extent.Text))
$TaskStart=[datetime]::Parse('2026-10-02T06:34:47.1841270Z').ToUniversalTime()
function Get-Process {
    param([int]$Id,[string]$ErrorAction)
    if($Id -eq 41){return [pscustomobject]@{Id=41;StartTime=$TaskStart}}
    return $null
}
$TaskMatchingTimes=@(
    '2026-10-02T06:34:47.1841270Z',
    '2026-10-02T06:34:47.184127Z',
    '2026-10-02T15:34:47.1841270+09:00',
    ('{"started_at":"2026-10-02T06:34:47.1841270Z"}' | ConvertFrom-Json).started_at
)
foreach($TaskTime in $TaskMatchingTimes){
    $TaskResult=Own-Process ([pscustomobject]@{pid=41;name='test-process';started_at=$TaskTime})
    if($TaskResult.Id -ne 41){throw 'Equivalent start instant did not match'}
}
$TaskRejected=$false
try{Own-Process ([pscustomobject]@{pid=41;name='test-process';started_at='2026-10-02T06:34:47.1841271Z'}) | Out-Null}catch{
    if($_.Exception.Message -notlike 'Recorded PID was reused:*'){throw}
    $TaskRejected=$true
}
if(-not $TaskRejected){throw 'A different start instant must reject a reused PID'}
if($null -ne (Own-Process ([pscustomobject]@{pid=42;name='missing';started_at=$TaskMatchingTimes[0]}))){throw 'A missing process must return null'}
Write-Output 'Process ownership: 6 checks passed; no service mutation'
