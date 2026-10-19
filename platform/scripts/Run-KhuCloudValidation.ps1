param(
    [ValidateSet('Review','Execute')][string]$Mode='Review',
    [ValidateSet('cpu','gpu')][string]$Kind='cpu',
    [switch]$ApproveCost,
    [switch]$VmCountCampaign,
    [double]$ComputeBudgetUsd=0,
    [ValidateSet(0,180)][int]$SshSettleSeconds=0,
    [ValidateSet('direct','lab','ssm')][string]$SshRoute='ssm',
    [ValidateSet('','us-west-2b','us-west-2c','us-west-2d')][string]$AvailabilityZone='',
    [ValidateSet('','m6a.2xlarge','g6.2xlarge','g6.8xlarge','g5.xlarge')][string]$CandidateId='',
    [ValidateRange(900,2400)][int]$LifetimeSeconds=2400
)
$ErrorActionPreference='Stop'
$TaskWorkspace=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$TaskLauncher=Join-Path $PSScriptRoot 'Run-KhuResearchPoc.ps1'
$TaskValidator=Join-Path $PSScriptRoot 'validate-cloud-vm.mjs'
if($VmCountCampaign){
    if($Kind -ne 'cpu' -or $CandidateId -notin @('','m6a.2xlarge') -or $LifetimeSeconds -ne 2400 -or $AvailabilityZone -or $SshRoute -ne 'ssm'){throw 'VM count validation requires CPU m6a.2xlarge, 40 minutes, default zone and SSM.'}
    $TaskValidator=Join-Path $PSScriptRoot 'validate-cloud-campaign.mjs'
}
$TaskConfigPath=Join-Path $TaskWorkspace 'platform\config\cloud-connection.json'
$TaskProcessPath=Join-Path $TaskWorkspace 'output\khu-platform-validation\runtime\processes.json'
$TaskCloudJournal=Join-Path $TaskWorkspace 'output\khu-platform-validation\cloud-journal'
. (Join-Path $PSScriptRoot 'CloudGroupGuard.ps1')
function Convert-TaskApiItems($TaskValue,[string[]]$TaskFields){
    if($null -eq $TaskValue){return}
    if($TaskValue -is [array]){return $TaskValue}
    foreach($TaskField in $TaskFields){if($TaskValue.PSObject.Properties.Name -contains $TaskField){return @($TaskValue.$TaskField)}}
    throw 'Unrecognized deployment list response. No services were restarted and no VM was created.'
}
$TaskNodeCandidates=@()
$TaskNodeCommand=Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if($TaskNodeCommand){$TaskNodeCandidates+=@($TaskNodeCommand.Source)}
if(Test-Path -LiteralPath $TaskProcessPath){$TaskNodeCandidates+=@(Get-Content -LiteralPath $TaskProcessPath -Raw | ConvertFrom-Json | Where-Object {$_.name -eq 'platform'} | ForEach-Object {$_.executable})}
$TaskNodeCandidates+=@((Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'),(Join-Path $env:ProgramFiles 'nodejs\node.exe'),(Join-Path $env:LOCALAPPDATA 'Programs\nodejs\node.exe'))
$TaskNode=$TaskNodeCandidates | Where-Object {$_ -and [IO.Path]::IsPathRooted($_) -and [IO.Path]::GetFileName($_) -ieq 'node.exe' -and (Test-Path -LiteralPath $_ -PathType Leaf)} | Select-Object -First 1
if(-not $TaskNode){throw 'Existing PoC Node runtime is unavailable.'}
$TaskZoneArgs=@();if($AvailabilityZone){$TaskZoneArgs=@('--availability-zone',$AvailabilityZone)}
$TaskValidationArgs=@('--lifetime-seconds',[string]$LifetimeSeconds);if($CandidateId){$TaskValidationArgs+=@('--candidate-id',$CandidateId)}
if($Mode -eq 'Review'){
    if($VmCountCampaign){& $TaskNode $TaskValidator}else{& $TaskNode $TaskValidator --kind $Kind @TaskZoneArgs @TaskValidationArgs}
    exit $LASTEXITCODE
}
if(-not $ApproveCost -or $ComputeBudgetUsd -le 0){throw 'Execute requires -ApproveCost and a reviewed positive -ComputeBudgetUsd. No VM was created.'}
$TaskReady=Invoke-RestMethod 'http://127.0.0.1:18100/api/v1/cloud/readiness' -TimeoutSec 10
if(@($TaskReady.pending_cleanup).Count -ne 0){throw 'Previous provider cleanup must finish first.'}
$TaskGroups=@(Convert-TaskApiItems (Invoke-RestMethod 'http://127.0.0.1:18150/api/v1/deployment-groups' -TimeoutSec 10) @('items','groups'))
$TaskBlockingGroups=@($TaskGroups | Where-Object {($_.operation_state -ne 'STOPPED' -or $_.actual_replicas -ne 0) -and -not (Test-KhuCloudGroupCleaned -Group $_ -JournalDirectory $TaskCloudJournal)})
if($TaskBlockingGroups.Count -gt 0){throw 'An existing deployment group is active or uncertain. No services were restarted and no VM was created.'}
$TaskDeployments=@(Convert-TaskApiItems (Invoke-RestMethod 'http://127.0.0.1:18150/api/v1/deployments' -TimeoutSec 10) @('items','deployments'))
if(@($TaskDeployments | Where-Object {-not $_.group_id -and $_.status -notin @('STOPPED','COMPLETED','RUNTIME_FAILED','DEPLOYMENT_FAILED','VALIDATION_FAILED','SCHEDULING_FAILED')}).Count -gt 0){throw 'An existing standalone deployment is active or uncertain. No services were restarted and no VM was created.'}
$TaskOriginal=[IO.File]::ReadAllText($TaskConfigPath)
$TaskOriginalSshSettle=[Environment]::GetEnvironmentVariable('KHU_VM_SSH_SETTLE_MS','Process')
$TaskOriginalSshRoute=[Environment]::GetEnvironmentVariable('KHU_VM_SSH_ROUTE','Process')
$TaskOriginalGpuFallback=[Environment]::GetEnvironmentVariable('KHU_CLOUD_GPU_FALLBACK_ENABLED','Process')
$TaskConnection=$TaskOriginal | ConvertFrom-Json
if($TaskConnection.enabled -eq $true){throw 'Cloud execution is already enabled. Check its existing session before changing the configuration.'}
$TaskExit=2
$TaskDispatched=$false
$TaskStartedCloud=$false
$TaskHealthLog=Join-Path $TaskWorkspace 'output\khu-platform-validation\runtime\appdeploy.stderr.log'
function Invoke-TaskPocLauncher([string]$TaskAction){
    & powershell -NoProfile -ExecutionPolicy Bypass -File $TaskLauncher -Action $TaskAction
    if($LASTEXITCODE -ne 0){throw "Local PoC launcher failed: $TaskAction"}
}
try{
    $env:KHU_CLOUD_GPU_FALLBACK_ENABLED=if($CandidateId -eq 'g5.xlarge'){'true'}else{'false'}
    $env:KHU_VM_SSH_SETTLE_MS=[string]($SshSettleSeconds*1000)
    $env:KHU_VM_SSH_ROUTE=$SshRoute
    if($SshRoute -eq 'ssm'){
        $TaskPlugin=Join-Path $TaskWorkspace 'output\khu-platform-validation\tools\session-manager-plugin-source\bin\session-manager-plugin.exe'
        if(-not (Test-Path -LiteralPath $TaskPlugin -PathType Leaf)){throw 'Prepared SSM plugin is unavailable. No VM was created.'}
        $TaskConnection | Add-Member -NotePropertyName management_transport -NotePropertyValue 'ssm' -Force
        $TaskConnection | Add-Member -NotePropertyName ssm_plugin -NotePropertyValue $TaskPlugin -Force
    }
    $TaskConnection.enabled=$true
    [IO.File]::WriteAllText($TaskConfigPath,($TaskConnection | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
    Invoke-TaskPocLauncher 'Stop'
    Start-Sleep -Seconds 12
    Invoke-TaskPocLauncher 'Start'
    $TaskStartedCloud=$true
    $TaskConfigured=Invoke-RestMethod 'http://127.0.0.1:18100/api/v1/cloud/readiness' -TimeoutSec 10
    if($TaskConfigured.ssh_settle_seconds -ne $SshSettleSeconds){throw 'Platform did not load the requested SSH settle setting. No VM was created.'}
    $TaskBudgetText=$ComputeBudgetUsd.ToString('R',[Globalization.CultureInfo]::InvariantCulture)
    $TaskDispatched=$true
    if($VmCountCampaign){& $TaskNode $TaskValidator --execute-approved --compute-budget-usd $TaskBudgetText}else{& $TaskNode $TaskValidator --kind $Kind --execute-approved --compute-budget-usd $TaskBudgetText @TaskZoneArgs @TaskValidationArgs}
    $TaskExit=$LASTEXITCODE
}finally{
    [Environment]::SetEnvironmentVariable('KHU_CLOUD_GPU_FALLBACK_ENABLED',$TaskOriginalGpuFallback,'Process')
    [Environment]::SetEnvironmentVariable('KHU_VM_SSH_SETTLE_MS',$TaskOriginalSshSettle,'Process')
    [Environment]::SetEnvironmentVariable('KHU_VM_SSH_ROUTE',$TaskOriginalSshRoute,'Process')
    if($TaskDispatched -and (Test-Path -LiteralPath $TaskHealthLog)){
        try{
            $TaskHealthEvents=@(Get-Content -LiteralPath $TaskHealthLog | ForEach-Object {
                try{
                    $TaskEvent=$_ | ConvertFrom-Json
                    if($TaskEvent.component -eq 'group-health' -and $TaskEvent.reason -in @('worker_identity_mismatch','http_status_unexpected','proxy_error')){
                        [pscustomobject]@{time=$TaskEvent.time;group_id=$TaskEvent.group_id;deployment_id=$TaskEvent.deployment_id;reason=$TaskEvent.reason;last_http_status=$TaskEvent.last_http_status;attempts=$TaskEvent.attempts}
                    }
                    if($TaskEvent.component -eq 'ssh-runner' -and $TaskEvent.reason -in @('TIMEOUT','CANCELED','EOF','RESET','ABORTED','AUTHENTICATION','HOST_KEY','REMOTE_EXIT','REFUSED','CLOSED','CHANNEL_REJECTED','OTHER')){
                        [pscustomobject]@{time=$TaskEvent.time;target_profile_id=$TaskEvent.target_profile_id;phase=$TaskEvent.phase;reason=$TaskEvent.reason;command_kind=$TaskEvent.command_kind;elapsed_ms=$TaskEvent.elapsed_ms;exit_status=$TaskEvent.exit_status;server_data=if($TaskEvent.server_data -in @('NONE','BANNER_RANGE','EARLY_HANDSHAKE','EXTENDED_HANDSHAKE')){$TaskEvent.server_data}else{$null}}
                    }
                    if($TaskEvent.component -eq 'ssh-handshake' -and $TaskEvent.reason -in @('EOF','RESET','ABORTED','CLOSED')){
                        [pscustomobject]@{time=$TaskEvent.time;phase='HANDSHAKE_RETRY';reason=$TaskEvent.reason;retry=$TaskEvent.retry;via_proxy=$TaskEvent.via_proxy;server_data=if($TaskEvent.server_data -in @('NONE','BANNER_RANGE','EARLY_HANDSHAKE','EXTENDED_HANDSHAKE')){$TaskEvent.server_data}else{$null}}
                    }
                }catch{}
            })
            if($TaskHealthEvents.Count -gt 0){
                $TaskHealthEvidence=Join-Path $TaskWorkspace ('output\khu-platform-validation\cloud-live-validation\appdeploy-health-'+(Get-Date -Format 'yyyy-MM-ddTHH-mm-ss')+'.json')
                [IO.File]::WriteAllText($TaskHealthEvidence,($TaskHealthEvents | ConvertTo-Json -Depth 4),[Text.UTF8Encoding]::new($false))
                Write-Output "Sanitized AppDeploy health evidence: $TaskHealthEvidence"
            }
        }catch{Write-Warning 'Sanitized AppDeploy health evidence could not be saved.'}
    }
    $TaskMayRestore=-not $TaskDispatched
    if($TaskDispatched){
        try{
            $TaskFinalReady=Invoke-RestMethod 'http://127.0.0.1:18100/api/v1/cloud/readiness' -TimeoutSec 10
            $TaskMayRestore=@($TaskFinalReady.pending_cleanup).Count -eq 0
        }catch{$TaskMayRestore=$false}
    }
    if($TaskMayRestore){
        if($TaskStartedCloud){
            Invoke-TaskPocLauncher 'Stop'
        }
        [IO.File]::WriteAllText($TaskConfigPath,$TaskOriginal,[Text.UTF8Encoding]::new($false))
        if($TaskStartedCloud){
            Start-Sleep -Seconds 12
            Invoke-TaskPocLauncher 'Start'
        }
    }else{
        Write-Warning 'Provider cleanup is unconfirmed. The cleanup watcher and local services remain running. Do not start the next VM; send the result JSON for inspection.'
        $TaskExit=2
    }
}
exit $TaskExit
