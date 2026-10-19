$ErrorActionPreference='Stop'
. (Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\CloudGroupGuard.ps1')
$TestDirectory=Join-Path ([IO.Path]::GetTempPath()) ('khu-cloud-group-guard-'+[guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($TestDirectory) | Out-Null
$RunId='run-11111111-2222-3333-4444-555555555555'
$Path=Join-Path $TestDirectory ($RunId+'.json')
$Group=[pscustomobject]@{flow_id='flow-test';app_version_id='appver-test';target_profile_id='khu-vm-'+$RunId.Substring(4);operation_state='UNKNOWN';actual_replicas=0}
$Run=[pscustomobject]@{run_id=$RunId;intent=[pscustomobject]@{flow_id='flow-test'};app=[pscustomobject]@{app_version_id='appver-test';target_profile_id=$Group.target_profile_id};state='CLEANED';cleanup=[pscustomobject]@{confirmed=$true;attachments=[pscustomobject]@{verified=$true;remaining=@()}};last_provider_status=[pscustomobject]@{state='Terminated';instance_id='i-test'};resource=[pscustomobject]@{provider_instance_id='i-test'}}
try {
    $Run | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
    if(-not (Test-KhuCloudGroupCleaned -Group $Group -JournalDirectory $TestDirectory)){throw 'Provider-confirmed exact group was blocked'}
    $WrongGroup=[pscustomobject]@{flow_id='flow-other';app_version_id=$Group.app_version_id;target_profile_id=$Group.target_profile_id}
    if(Test-KhuCloudGroupCleaned -Group $WrongGroup -JournalDirectory $TestDirectory){throw 'Mismatched Flow was accepted'}
    $Run.cleanup.attachments.remaining=@([pscustomobject]@{type='volume';id='vol-test'})
    $Run | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
    if(Test-KhuCloudGroupCleaned -Group $Group -JournalDirectory $TestDirectory){throw 'Billable attachment was ignored'}
    $Run.cleanup.attachments.remaining=@();$Run.last_provider_status.state='Running'
    $Run | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Path -Encoding UTF8
    if(Test-KhuCloudGroupCleaned -Group $Group -JournalDirectory $TestDirectory){throw 'Live provider was ignored'}
    'Cloud group guard: OK'
} finally {
    Remove-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $TestDirectory -ErrorAction SilentlyContinue
}
