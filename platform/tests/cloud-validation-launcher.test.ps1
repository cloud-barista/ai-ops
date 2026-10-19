$ErrorActionPreference='Stop'
$TaskTokens=$null;$TaskErrors=$null
$TaskScript=Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\Run-KhuCloudValidation.ps1'
$TaskAst=[Management.Automation.Language.Parser]::ParseFile($TaskScript,[ref]$TaskTokens,[ref]$TaskErrors)
if($TaskErrors.Count){throw 'Launcher parse failed'}
$TaskFunction=$TaskAst.Find({param($TaskNode) $TaskNode -is [Management.Automation.Language.FunctionDefinitionAst] -and $TaskNode.Name -eq 'Convert-TaskApiItems'},$true)
if(-not $TaskFunction){throw 'API collection normalization is missing'}
Invoke-Expression $TaskFunction.Extent.Text
function Assert-Task([bool]$TaskCondition,[string]$TaskName){if(-not $TaskCondition){throw $TaskName};Write-Output "PASS: $TaskName"}
Assert-Task (@(Convert-TaskApiItems ([pscustomobject]@{deployments=@()}) @('items','deployments')).Count -eq 0) 'empty deployment envelope is not an unknown deployment'
Assert-Task (@(Convert-TaskApiItems @([pscustomobject]@{group_id='group-1'},[pscustomobject]@{group_id='group-2'}) @('items','groups')).Count -eq 2) 'direct group list preserves members'
Assert-Task (@(Convert-TaskApiItems ([pscustomobject]@{items=@([pscustomobject]@{deployment_id='dep-1'})}) @('items','deployments')).Count -eq 1) 'items envelope preserves the active deployment'
$TaskRejected=$false
try{Convert-TaskApiItems ([pscustomobject]@{unexpected='shape'}) @('items','deployments')}catch{$TaskRejected=$true}
Assert-Task $TaskRejected 'unrecognized response cannot bypass active deployment checks'
