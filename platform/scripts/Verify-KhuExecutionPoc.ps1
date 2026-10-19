param([switch]$LiveCloud,[string]$FlowId)
$ErrorActionPreference='Stop'
$TaskRoot=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if($LiveCloud){
 if($FlowId -notmatch '^flow-[a-zA-Z0-9-]+$'){throw 'A specific reviewed FlowId is required.'}
 $TaskBase='http://127.0.0.1:18100/api/v1/flows/'+$FlowId
 $TaskFlow=Invoke-RestMethod $TaskBase -TimeoutSec 10
 if($TaskFlow.cloud.state -ne 'READY' -or -not $TaskFlow.cloud.approval -or $TaskFlow.cloud.approval.consumed_at -or [datetimeoffset]$TaskFlow.cloud.approval.expires_at -le [datetimeoffset]::UtcNow){throw 'Current reviewed approval and successful preflight are required. This script never grants approval.'}
 try{
  $null=Invoke-RestMethod ($TaskBase+'/cloud/create') -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 10
  $TaskLimit=[datetimeoffset]::UtcNow.AddMinutes(46)
  do {Start-Sleep -Seconds 10;$TaskFlow=Invoke-RestMethod ($TaskBase+'/cloud/status') -TimeoutSec 45;if($TaskFlow.cloud.state -in @('CLEANUP_PENDING','CLEANED','CREATE_UNKNOWN')){throw 'Cloud pipeline did not reach a confirmed running app.'}} while($TaskFlow.cloud.state -ne 'RUNNING' -and [datetimeoffset]::UtcNow -lt $TaskLimit)
  if($TaskFlow.cloud.state -ne 'RUNNING'){throw 'Cloud app did not become ready within its deadline.'}
  $TaskBody=@{selection_version=$TaskFlow.selection_version} | ConvertTo-Json -Compress
  $TaskFlow=Invoke-RestMethod ($TaskBase+'/appdeploy/measure') -Method Post -ContentType 'application/json' -Body $TaskBody -TimeoutSec 100
  $TaskFlow.measurement | ConvertTo-Json -Depth 15
 }finally{
  $TaskClean=Invoke-RestMethod ($TaskBase+'/cloud/cleanup') -Method Post -ContentType 'application/json' -Body '{}' -TimeoutSec 120
  Write-Output ('Cleanup: '+$TaskClean.cloud.state+'; independent watcher retains pending resources.')
 }
 exit 0
}
$TaskNode=(Get-Command node -ErrorAction Stop).Source
Push-Location $TaskRoot
try{
 & $TaskNode --test --test-concurrency=1 platform/tests/*.test.mjs
 if($LASTEXITCODE -ne 0){throw 'Node checks failed'}
 & (Join-Path $TaskRoot 'platform\tests\launcher-process-ownership.test.ps1')
 $TaskHealth=Invoke-RestMethod 'http://127.0.0.1:18100/healthz' -TimeoutSec 5
 if($TaskHealth.service -ne 'khu-research-poc'){throw 'Canonical KHU runtime is unavailable'}
 Write-Output 'Local-only checks complete. No VM was created; use the UI for actual app measurement.'
}finally{Pop-Location}
