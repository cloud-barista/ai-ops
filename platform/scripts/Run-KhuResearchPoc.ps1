param([ValidateSet('Start','Status','Stop')][string]$Action='Start',[string]$AppDeployModule='external\AppDeploy')
$ErrorActionPreference='Stop'
$Workspace=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Runtime=Join-Path $Workspace 'output\khu-platform-validation\runtime'
$Record=Join-Path $Runtime 'processes.json'
New-Item -ItemType Directory -Path $Runtime -Force | Out-Null
function Own-Process($Entry) {
    $Proc=Get-Process -Id $Entry.pid -ErrorAction SilentlyContinue
    if ($null -eq $Proc) { return $null }
    $RecordedStart=[datetimeoffset]$Entry.started_at
    if ($Proc.StartTime.ToUniversalTime().Ticks -ne $RecordedStart.UtcDateTime.Ticks) { throw "Recorded PID was reused: $($Entry.name)" }
    if ($Entry.executable -and $Proc.Path -ne $Entry.executable) { throw "Recorded executable changed: $($Entry.name)" }
    return $Proc
}
function Resolve-PocNode([object[]]$RecordedEntries=@(),[string[]]$FallbackPaths=@()) {
    $Command=Get-Command node -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $Candidates=@()
    if($Command){$Candidates+=@($Command.Source)}
    $Candidates+=@($RecordedEntries | Where-Object {$_.name -in @('platform','cloud-cleanup-watcher','reference-agent','demo-observations')} | ForEach-Object {$_.executable})
    $Candidates+=@($FallbackPaths)
    foreach($Candidate in $Candidates){
        if(-not $Candidate -or -not [IO.Path]::IsPathRooted($Candidate) -or [IO.Path]::GetFileName($Candidate) -ine 'node.exe'){continue}
        if(Test-Path -LiteralPath $Candidate -PathType Leaf){return (Resolve-Path -LiteralPath $Candidate).Path}
    }
    throw 'Node runtime not found. Restore the existing PoC Node runtime or install Node.js; no service was stopped.'
}
$Entries=@()
if (Test-Path -LiteralPath $Record) { $Entries=Get-Content -LiteralPath $Record -Raw | ConvertFrom-Json }
$NodeFallbacks=@(
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'),
    (Join-Path $env:ProgramFiles 'nodejs\node.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\nodejs\node.exe')
)
function Close-PocSsmRelay($Entry,[string]$NodeRuntime) {
    if(-not (Own-Process $Entry)){return}
    & $NodeRuntime (Join-Path $Workspace 'platform\scripts\ssm-ssh-relay.mjs') --stop-idle
    $TaskControlExit=$LASTEXITCODE
    if($TaskControlExit -eq 3){
        # Legacy or already-closed control endpoint: force-stop only if there
        # is no owned AWS child and no remaining port-forward listener.
        $TaskChildren=@(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($Entry.pid)" | Where-Object {$_.Name -eq 'aws.exe'})
        $TaskForward=Get-NetTCPConnection -LocalPort 18197,18198 -State Listen -ErrorAction SilentlyContinue
        if($TaskChildren.Count -gt 0 -or $TaskForward){throw 'SSM child shutdown is unconfirmed. Relay and services were retained.'}
        return
    }
    if($TaskControlExit -ne 0){throw 'SSM graceful shutdown failed. Relay and services were retained.'}
}
if ($Action -eq 'Status') {
    foreach ($Entry in $Entries) { $Proc=Own-Process $Entry; Write-Output "$($Entry.name): $(if ($Proc) {'running'} else {'stopped'}) / port $($Entry.port)" }
    exit 0
}
if ($Action -eq 'Stop') {
    $Node=Resolve-PocNode -RecordedEntries $Entries -FallbackPaths $NodeFallbacks
    $AppEntry=$Entries | Where-Object { $_.name -eq 'appdeploy' }
    if ($AppEntry -and (Own-Process $AppEntry)) {
        $StopResult=& $Node (Join-Path $Workspace 'platform\server\runtime-reconcile.mjs') stop
        if($LASTEXITCODE -ne 0){throw 'An owned app stop is unconfirmed. Services were retained; check AppDeploy state.'}
        Write-Output $StopResult
    }
    foreach($TaskSsmEntry in @($Entries | Where-Object {$_.name -eq 'cloud-ssm-relay'})){Close-PocSsmRelay $TaskSsmEntry $Node}
    # The watcher exits only after its journal proves cleanup, never via this PID loop.
    foreach ($Entry in $Entries | Where-Object {$_.name -ne 'cloud-cleanup-watcher'}) { $Proc=Own-Process $Entry; if ($Proc) { Stop-Process -Id $Proc.Id; Write-Output "Stopped own process: $($Entry.name)" } }
    exit 0
}
$Ports=@(18100,18110,18120,18130,18140,18150,18170,18185,18190)
if($env:KHU_VM_SSH_ROUTE -in @('lab','ssm')){$Ports+=18195}
$PrometheusReady=Join-Path $Runtime 'prometheus\ready.json'
if(-not (Test-Path -LiteralPath $PrometheusReady)){throw 'Run platform\scripts\Setup-Prometheus.ps1 once to prepare the local monitoring server.'}
$Prometheus=Get-Content -LiteralPath $PrometheusReady -Raw | ConvertFrom-Json
if(-not (Test-Path -LiteralPath $Prometheus.binary)){throw 'The local Prometheus binary is missing.'}
$PolicyDir=Join-Path $Workspace 'output\khu-platform-validation\research-policy'
$PolicyReport=Join-Path $PolicyDir 'research-cases.json'
if(-not (Test-Path -LiteralPath $PolicyReport)) { throw 'Prepare the original research policy with platform/research_policy.py build first. See platform/README.md.' }
$PolicyPython=(Get-Content -LiteralPath $PolicyReport -Raw -Encoding UTF8 | ConvertFrom-Json).python
if(-not (Test-Path -LiteralPath $PolicyPython)) { throw 'The Python runtime used to prepare the research policy is unavailable.' }
$AiPython=Join-Path $Workspace 'output\khu-platform-validation\ai-venv\Scripts\python.exe'
$ModelReady=Join-Path $Workspace 'output\khu-platform-validation\ai-models\ready.json'
if(-not (Test-Path -LiteralPath $AiPython) -or -not (Test-Path -LiteralPath $ModelReady)) { throw 'Run platform\scripts\Setup-LocalAiRuntime.ps1 to prepare the dedicated BERT Tiny runtime first.' }
$Model=Get-Content -LiteralPath $ModelReady -Raw | ConvertFrom-Json
if($Model.model_id -ne 'google/bert_uncased_L-2_H-128_A-2' -or $Model.model_revision -ne '30b0a37ccaaa32f332884b96992754e246e48c5f') { throw 'Pinned model cache does not match the approved local model.' }
foreach ($Port in $Ports) {
    $Listener=Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($Listener) { throw "Port $Port is occupied. Use -Action Status; no existing process was stopped." }
}
$Go=(Get-Command go -ErrorAction Stop).Source
$Node=Resolve-PocNode -RecordedEntries $Entries -FallbackPaths $NodeFallbacks
$AppDeployModule=(Resolve-Path -LiteralPath $AppDeployModule).Path
if (-not (Test-Path -LiteralPath (Join-Path $AppDeployModule 'cmd\server\main.go'))) { throw 'The selected AppDeploy server source is missing.' }
$Overlay=Join-Path $Runtime 'appdeploy-overlay.json'
$Replace=@{};$Replace[(Join-Path $AppDeployModule 'cmd\server\main.go')]=(Join-Path $Workspace 'platform\scripts\appdeploy-loopback-main.go')
@{Replace=$Replace} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Overlay -Encoding Ascii
Push-Location $AppDeployModule
try { & $Go build -overlay $Overlay -o (Join-Path $Runtime 'appdeploy.exe') ./cmd/server; if($LASTEXITCODE -ne 0){throw 'AppDeploy build failed'}; $SourceRevision=& git rev-parse HEAD } finally { Pop-Location }
@{module=$AppDeployModule;revision=$SourceRevision;overlay=$Overlay;binary_sha256=(Get-FileHash -LiteralPath (Join-Path $Runtime 'appdeploy.exe')).Hash.ToLowerInvariant();source_edits_by_launcher=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Runtime 'appdeploy-source.json') -Encoding UTF8
$Services=@(
    @{name='llmop';repo='llmop';module='llmop\go\service-control-api';target='./cmd/llmop-api';port=18110},
    @{name='resource_ops';repo='resource_ops';module='resource_ops';target='./cmd/server';port=18120},
    @{name='geon';repo='deployment_agent';module='deployment_agent\go\service-control-api';target='./cmd/service-control-api';port=18130}
)
foreach ($Service in $Services) {
    Write-Output "Building $($Service.name) from this workspace..."
    Push-Location (Join-Path $Workspace $Service.module)
    try { & $Go build -o (Join-Path $Runtime ($Service.name+'.exe')) $Service.target; if ($LASTEXITCODE -ne 0) { throw "Build failed: $($Service.name)" } } finally { Pop-Location }
}
$Names=@('KHU_PROMETHEUS_URL','KHU_POLICY_MODE','KHU_POLICY_URL','PORT','HOST','AIOPS_BIND_ADDRESS','AIOPS_REPO_ROOT','RESOURCE_PROMETHEUS_URL','AIOPS_APPDEPLOY_SUBMIT_ENABLED','AIOPS_APPDEPLOY_BASE_URL','AIOPS_LLMOP_ALLOW_LIVE_COMPLETION','AIOPS_DEPLOYMENT_ADAPTER','AIOPS_LEGACY_API_ENABLED','RESOURCE_PROMETHEUS_BEARER_TOKEN','AIOPS_LLM_CANDIDATES_PATH','AIOPS_LLMOP_CANDIDATE_ID','AIOPS_APPDEPLOY_DELIVERY_DIR','RESOURCE_PROVIDER','PLACEMENT_PROVIDER','AIAPP_CONFIG_PATH','AIAPP_SERVER_PORT','AIAPP_CPUVM_RUNNER','AIAPP_GPUVM_RUNNER','AIAPP_STORE_PATH','AIAPP_LOCAL_RUNTIME_WORK_DIR','AIAPP_AIOPS_ROOT','AIAPP_PACKAGE_OUTPUT_DIR','AIAPP_CREDENTIAL_API_ALLOW_REMOTE','AIAPP_SSH_SOCKS_PROXY')
$Saved=@{};foreach($Name in $Names){$Saved[$Name]=[Environment]::GetEnvironmentVariable($Name,'Process')}
$Started=@($Entries | Where-Object {$_.name -eq 'cloud-cleanup-watcher' -and (Own-Process $_)})
function Spawn($Name,$Executable,$Arguments,$Directory,$Port) {
    $Params=@{FilePath=$Executable;WorkingDirectory=$Directory;WindowStyle='Hidden';PassThru=$true;RedirectStandardOutput=(Join-Path $Runtime ($Name+'.stdout.log'));RedirectStandardError=(Join-Path $Runtime ($Name+'.stderr.log'))}
    if($Arguments){$Params.ArgumentList=$Arguments}
    $Proc=Start-Process @Params
    $script:Started+=@{name=$Name;pid=$Proc.Id;started_at=$Proc.StartTime.ToUniversalTime().ToString('o');executable=$Executable;port=$Port}
    $script:Started | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $Record -Encoding UTF8
}
try {
    $env:KHU_PROMETHEUS_URL='http://127.0.0.1:18190'
    $PromConfig=$Prometheus.config.Replace('\','/');$PromData=(Join-Path $Runtime 'prometheus\data').Replace('\','/')
    $Discovery=Join-Path $Workspace 'platform\monitoring\replica-targets.json';if(-not(Test-Path -LiteralPath $Discovery)){'[]' | Set-Content -LiteralPath $Discovery -Encoding Ascii}
    Spawn 'prometheus' $Prometheus.binary @('--config.file="'+$PromConfig+'"','--web.listen-address=127.0.0.1:18190','--storage.tsdb.path="'+$PromData+'"','--storage.tsdb.retention.time=1d','--storage.tsdb.retention.size=64MB','--storage.tsdb.min-block-duration=1h','--storage.tsdb.max-block-duration=1h') (Join-Path $Workspace 'platform\monitoring') 18190
    $env:KHU_POLICY_MODE='shadow';$env:KHU_POLICY_URL='http://127.0.0.1:18170'
    Spawn 'research-policy' $PolicyPython @('-X','utf8',('"'+(Join-Path $Workspace 'platform\research_policy.py')+'"'),'serve','--artifact-dir',('"'+$PolicyDir+'"')) $Workspace 18170
    $Ready=$false;for($Attempt=0;$Attempt -lt 20;$Attempt++){try{$null=Invoke-RestMethod 'http://127.0.0.1:18170/healthz' -TimeoutSec 1;$Ready=$true;break}catch{Start-Sleep -Milliseconds 200}};if(-not $Ready){throw 'Original research policy service did not become ready. See research-policy.stderr.log.'}
    $env:HOST='127.0.0.1';$env:AIOPS_BIND_ADDRESS='127.0.0.1';$env:RESOURCE_PROMETHEUS_URL='http://127.0.0.1:18140';$env:RESOURCE_PROMETHEUS_BEARER_TOKEN=''
    $env:AIOPS_LLM_CANDIDATES_PATH=Join-Path $Workspace 'llmop\config\ops_llm_eval_candidates.local_ollama.json';$env:AIOPS_LLMOP_CANDIDATE_ID='qwen3.5-profile-generator'
    $env:RESOURCE_PROVIDER='local';$env:PLACEMENT_PROVIDER='local';$env:AIAPP_CONFIG_PATH='';$env:AIAPP_SERVER_PORT='18150';$env:AIAPP_CPUVM_RUNNER='dry-run';$env:AIAPP_GPUVM_RUNNER='dry-run';$env:AIAPP_CREDENTIAL_API_ALLOW_REMOTE='false'
    $CloudConfig=Join-Path $Workspace 'platform\config\cloud-connection.json'
    $env:AIAPP_SSH_SOCKS_PROXY=''
    if(Test-Path -LiteralPath $CloudConfig){$Cloud=Get-Content -LiteralPath $CloudConfig -Raw | ConvertFrom-Json;if($Cloud.enabled -eq $true){
        $env:AIAPP_CPUVM_RUNNER='ssh';$env:AIAPP_GPUVM_RUNNER='ssh'
        if($Cloud.management_transport -eq 'ssm'){
            if(-not (Test-Path -LiteralPath $Cloud.ssm_plugin -PathType Leaf)){throw 'Prepared SSM plugin is unavailable.'}
            if(Get-NetTCPConnection -LocalPort 18197,18198 -State Listen -ErrorAction SilentlyContinue){throw 'An SSM forwarding port is occupied. No VM was created.'}
            Spawn 'cloud-ssm-relay' $Node @('"'+(Join-Path $Workspace 'platform\scripts\ssm-ssh-relay.mjs')+'"') $Workspace 18195
            $TaskRelay=$script:Started | Where-Object name -eq 'cloud-ssm-relay' | Select-Object -Last 1
            $TaskRelayReady=$false
            for($TaskAttempt=0;$TaskAttempt -lt 40;$TaskAttempt++){
                $TaskListener=Get-NetTCPConnection -LocalPort 18195 -State Listen -ErrorAction SilentlyContinue
                if($TaskListener -and @($TaskListener | Where-Object OwningProcess -eq $TaskRelay.pid).Count -gt 0){$TaskRelayReady=$true;break}
                Start-Sleep -Milliseconds 250
            }
            if(-not $TaskRelayReady){throw 'Owned SSM relay did not become ready.'}
            $env:AIAPP_SSH_SOCKS_PROXY='127.0.0.1:18195'
            Write-Output 'Cloud runtime SSH route: pinned SSH through owned SSM port-forwarding session.'
        }
        elseif($env:KHU_VM_SSH_ROUTE -eq 'lab'){
            if(-not (Test-Path -LiteralPath $Cloud.ssh_key -PathType Leaf)){throw 'Existing lab SSH identity is unavailable.'}
            $TaskSsh=(Get-Command ssh -CommandType Application -ErrorAction Stop).Source
            Spawn 'cloud-ssh-relay' $TaskSsh @('-N','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3','-D','127.0.0.1:18195','-i',('"'+$Cloud.ssh_key+'"'),'-p','7877','lab-user@192.0.2.216') $Workspace 18195
            $TaskRelay=$script:Started | Where-Object name -eq 'cloud-ssh-relay' | Select-Object -Last 1
            $TaskRelayReady=$false
            for($TaskAttempt=0;$TaskAttempt -lt 40;$TaskAttempt++){
                $TaskListener=Get-NetTCPConnection -LocalPort 18195 -State Listen -ErrorAction SilentlyContinue
                if($TaskListener -and @($TaskListener | Where-Object OwningProcess -eq $TaskRelay.pid).Count -gt 0){$TaskRelayReady=$true;break}
                Start-Sleep -Milliseconds 250
            }
            if(-not $TaskRelayReady){throw 'Owned lab SSH relay did not become ready.'}
            $env:AIAPP_SSH_SOCKS_PROXY='127.0.0.1:18195'
            Write-Output 'Cloud runtime SSH route: local relay through authenticated lab server.'
        }
    }}
    $env:AIAPP_STORE_PATH=Join-Path $Runtime 'appdeploy-store.json';$env:AIAPP_LOCAL_RUNTIME_WORK_DIR=Join-Path $Runtime 'app-runtime';$env:AIAPP_AIOPS_ROOT=Join-Path $Workspace 'deployment_agent';$env:AIAPP_PACKAGE_OUTPUT_DIR=Join-Path $Runtime 'packages'
    Spawn 'appdeploy' (Join-Path $Runtime 'appdeploy.exe') @() $AppDeployModule 18150
    $Ready=$false;for($Attempt=0;$Attempt -lt 40;$Attempt++){try{$null=Invoke-RestMethod 'http://127.0.0.1:18150/api/v1/healthz' -TimeoutSec 2;$Ready=$true;break}catch{Start-Sleep -Milliseconds 300}};if(-not $Ready){throw 'AppDeploy did not become ready'}
    & $Node (Join-Path $Workspace 'platform\scripts\Register-LocalTarget.mjs');if($LASTEXITCODE -ne 0){throw 'Local target registration failed'}
    & $Node (Join-Path $Workspace 'platform\server\runtime-reconcile.mjs') reconcile;if($LASTEXITCODE -ne 0){throw 'Owned group reconciliation failed'}
    if(-not($Started | Where-Object {$_.name -eq 'cloud-cleanup-watcher'})){Spawn 'cloud-cleanup-watcher' $Node @('"'+(Join-Path $Workspace 'platform\server\cloud-watcher.mjs')+'"') $Workspace 0}
    $env:AIOPS_APPDEPLOY_SUBMIT_ENABLED='true';$env:AIOPS_APPDEPLOY_BASE_URL='http://127.0.0.1:18150/api/v1';$env:AIOPS_APPDEPLOY_DELIVERY_DIR=Join-Path $Runtime 'appdeploy-deliveries';$env:AIOPS_LLMOP_ALLOW_LIVE_COMPLETION='false';$env:AIOPS_DEPLOYMENT_ADAPTER='mock';$env:AIOPS_LEGACY_API_ENABLED='false'
    Spawn 'demo-observations' $Node @('"'+(Join-Path $Workspace 'platform\server\demo-observations.mjs')+'"') $Workspace 18140
    foreach($Service in $Services){$env:PORT=[string]$Service.port;$env:AIOPS_REPO_ROOT=Join-Path $Workspace $Service.repo;Spawn $Service.name (Join-Path $Runtime ($Service.name+'.exe')) @() (Join-Path $Workspace $Service.repo) $Service.port}
    Spawn 'reference-agent' $Node @('"'+(Join-Path $Workspace 'platform\runtime-agent\reference-agent.mjs')+'"') $Workspace 18185
    $env:PORT='18100';Spawn 'platform' $Node @('"'+(Join-Path $Workspace 'platform\server\index.mjs')+'"') $Workspace 18100
    foreach($Port in ($Ports | Where-Object {$_ -ne 18195})){$Path=if($Port -eq 18190){'/-/ready'}elseif($Port -eq 18150){'/api/v1/healthz'}elseif($Port -eq 18120 -or $Port -eq 18140){'/health'}else{'/healthz'};$Ready=$false;for($Attempt=0;$Attempt -lt 30;$Attempt++){try{$null=Invoke-RestMethod -Uri "http://127.0.0.1:$Port$Path" -TimeoutSec 2;$Ready=$true;break}catch{Start-Sleep -Milliseconds 300}};if(-not $Ready){throw "Service did not become ready on $Port. See $Runtime"}}
    Write-Output 'KHU Research PoC: http://127.0.0.1:18100/'
    Write-Output 'Local apps, Agent dispatch and Prometheus are connected. Startup creates no cloud VM; cloud execution requires its reviewed approval.'
}catch{
    $TaskRetainCloudRuntime=$false
    foreach($TaskSsmEntry in @($Started | Where-Object {$_.name -eq 'cloud-ssm-relay'})){try{Close-PocSsmRelay $TaskSsmEntry $Node}catch{$TaskRetainCloudRuntime=$true;Write-Warning 'SSM shutdown is unconfirmed; relay, AppDeploy and cleanup watcher were retained.'}}
    $TaskRetainedNames=if($TaskRetainCloudRuntime){@('cloud-cleanup-watcher','cloud-ssm-relay','appdeploy')}else{@('cloud-cleanup-watcher')}
    foreach($Entry in $Started | Where-Object {$_.name -notin $TaskRetainedNames}){$Proc=Own-Process $Entry;if($Proc){Stop-Process -Id $Proc.Id}}
    throw
}finally{foreach($Name in $Names){[Environment]::SetEnvironmentVariable($Name,$Saved[$Name],'Process')}}
