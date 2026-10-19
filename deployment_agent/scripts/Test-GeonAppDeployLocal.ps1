param(
    [Parameter(Mandatory = $true)][string]$AppDeployModule,
    [int]$GeonPort = 18089,
    [int]$AppDeployPort = 18088,
    [switch]$KeepRunning
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$module = (Resolve-Path -LiteralPath $AppDeployModule).Path
$run = Join-Path $repo ("tmp\integration-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $run | Out-Null
foreach ($port in @($GeonPort, $AppDeployPort)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "Port $port is occupied" }
}
function Build([string]$directory, [string]$binary, [string]$entry) {
    Push-Location $directory
    try { go build -o $binary $entry; if ($LASTEXITCODE -ne 0) { throw "Build failed" } } finally { Pop-Location }
}
function Post([string]$url, $body) {
    Invoke-RestMethod $url -Method Post -ContentType "application/json" -Body ($body | ConvertTo-Json -Depth 40)
}
function Wait-Health([string]$url) {
    for ($i = 0; $i -lt 40; $i++) {
        try { Invoke-RestMethod $url | Out-Null; return } catch { Start-Sleep -Milliseconds 250 }
    }
    throw "Health check failed: $url"
}
$appBinary = Join-Path $run "appdeploy.exe"
$geonBinary = Join-Path $run "geon.exe"
Build $module $appBinary "./cmd/server"
Build (Join-Path $repo "go\service-control-api") $geonBinary "./cmd/service-control-api"
$appServer = $null
$geonServer = $null
$created = $null
$success = $false
$appURL = "http://127.0.0.1:$AppDeployPort/api/v1"
$geonURL = "http://127.0.0.1:$GeonPort"
try {
    $env:RESOURCE_PROVIDER = "local"
    $env:PLACEMENT_PROVIDER = "local"
    $env:AIAPP_SERVER_PORT = "$AppDeployPort"
    $env:AIAPP_CONFIG_PATH = ""
    $env:AIAPP_CPUVM_RUNNER = "dry-run"
    $env:AIAPP_GPUVM_RUNNER = "dry-run"
    $env:AIAPP_LOCAL_FAULT_RATE = "0"
    $env:AIAPP_STORE_PATH = Join-Path $run "appdeploy-store.json"
    $env:AIAPP_LOCAL_RUNTIME_WORK_DIR = Join-Path $run "runtime"
    $appServer = Start-Process $appBinary -WorkingDirectory $module -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $run "appdeploy.log") -RedirectStandardError (Join-Path $run "appdeploy.err")
    Wait-Health "$appURL/healthz"
    $env:AIOPS_REPO_ROOT = $repo
    $env:AIOPS_BIND_ADDRESS = "127.0.0.1"
    $env:PORT = "$GeonPort"
    $env:AIOPS_APPDEPLOY_BASE_URL = $appURL
    $env:AIOPS_APPDEPLOY_SUBMIT_ENABLED = "true"
    $env:AIOPS_APPDEPLOY_DELIVERY_DIR = Join-Path $run "deliveries"
    $env:AIOPS_DEPLOYMENT_ADAPTER = "mock"
    $geonServer = Start-Process $geonBinary -WorkingDirectory $repo -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $run "geon.log") -RedirectStandardError (Join-Path $run "geon.err")
    Wait-Health "$geonURL/healthz"
    $appDir = Join-Path $module "examples\local-app"
    $app = Post "$appURL/apps" @{app_spec = @{
        schema_version = "appspec.khu.ai/v1alpha1"; kind = "AIApp"
        metadata = @{name = "geon-local-worker"; version = "1.0.0"}
        artifact = @{type = "script"; uri = ([Uri]$appDir).AbsoluteUri}
        entrypoint = @{command = "powershell.exe"; args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "run.ps1")}
        runtime = @{type = "cpu"}; resources = @{cpu = "1"; memory = "256Mi"; storage = "1Gi"}
    }}
    Post "$appURL/target-profiles" @{
        target_profile_id = "geon-local-node"; vm_type = "Local"; csp = "local"; status = "READY"; cost_weight = 1
        vm = @{}; runtime = @{runtime_type = "local"; operating_mode = "local_process"}
        supported_runtimes = @("cpu"); capacity = @{cpu_cores = 2; memory_bytes = 4294967296; storage_bytes = 10737418240}
        storage = @{artifact_dir = $appDir; log_dir = (Join-Path $run "runtime-logs")}
    } | Out-Null
    $id = "flow-local-" + [guid]::NewGuid().ToString("N")
    $trace = "trace-$id"
    $now = (Get-Date).ToUniversalTime().ToString("o")
    $context = @{
        contract_version = "1.0"; message_id = "msg-context-$id"; message_type = "application.context.created"
        occurred_at = $now; correlation_id = $id; trace_id = $trace
        source = @{system = "integration-fixture"; component = "requirements"}; target = @{system = "khu-geon"; component = "agent-control"}
        data = @{application_profile = @{
            profile_id = "profile-$id"; app_id = "geon-local-worker"; app_version = "1.0.0"
            requirements = @{compute = @{cpu_cores_min = 1; memory_mib_min = 256; storage_gib_min = 1}; accelerator = @{required = $false}; deployment = @{replicas_min = 1; replicas_max = 1; isolation = "ONE_MAJOR_APP_PER_VM"}}
        }}
    }
    $context.data.model_recommendation = @{inference_configuration = @{replicas = 1}}
    $recommendation = @{
        contract_version = "1.0"; message_id = "msg-resource-$id"; message_type = "resource.recommendation.created"
        occurred_at = $now; correlation_id = $id; trace_id = $trace; causation_id = "msg-context-$id"
        source = @{system = "integration-fixture"; component = "resources"}; target = @{system = "khu-geon"; component = "agent-control"}
        data = @{resource_recommendation = @{
            recommendation_id = "rec-$id"; profile_id = "profile-$id"; snapshot_id = "local-test"; status = "FOUND"; selected_candidate_id = "local-cpu"
            candidates = @(@{candidate_id = "local-cpu"; rank = 1; feasible = $true; desired_infrastructure = @{node_count = 1; cpu_cores_per_node = 1; memory_mib_per_node = 256; storage_gib_per_node = 1; isolation = "ONE_MAJOR_APP_PER_VM"}; scores = @{total = 1}})
        }}
    }
    $pair = @{application_context = $context; resource_recommendation = $recommendation}
    $pair | ConvertTo-Json -Depth 40 | Set-Content (Join-Path $run "external-input.json") -Encoding utf8
    $flow = Post "$geonURL/api/v1/agent-control/external-flows" $pair
    if ($flow.state -ne "DEPLOY_APPROVED") { throw "Expected DEPLOY_APPROVED, got $($flow.state)" }
    $request = @{app_version_id = $app.app_version_id; accept_projection_limits = $true}
    $path = "$geonURL/api/v1/agent-control/flows/$id/appdeploy"
    $prepared = Post "$path/prepare" $request
    $created = Post "$path/submit" $request
    $replay = Post "$path/submit" $request
    if ($created.deployment.deployment_id -ne $replay.deployment.deployment_id) { throw "Duplicate deployment" }
    $status = Invoke-RestMethod $path
    if ($status.flow.deployment_status.data.deployment_status.state -ne "RUNNING") { throw "Expected real local RUNNING" }
    $logs = Invoke-RestMethod "$appURL/deployments/$($created.deployment.deployment_id)/logs"
    $evidence = @{flow_id = $id; app_version_id = $app.app_version_id; geon_url = $geonURL; appdeploy_url = $appURL; geon_pid = $geonServer.Id; appdeploy_pid = $appServer.Id; prepared = $prepared; delivery = $status; logs = $logs; scope = "Real AppDeployer local_process; upstream is a contract fixture, no cloud VM"}
    $evidence | ConvertTo-Json -Depth 70 | Set-Content (Join-Path $run "evidence.json") -Encoding utf8
    $success = $true
    [pscustomobject]@{FlowID = $id; AppVersionID = $app.app_version_id; DeploymentID = $created.deployment.deployment_id; Status = $status.deployment.status; GeonURL = $geonURL; GeonPID = $geonServer.Id; AppDeployPID = $appServer.Id; Evidence = (Join-Path $run "evidence.json")}
} finally {
    if (-not ($KeepRunning -and $success)) {
        if ($created -and $created.deployment) { try { Post "$appURL/deployments/$($created.deployment.deployment_id)/stop" @{} | Out-Null } catch { Write-Warning "Stop failed; inspect local worker process" } }
        foreach ($process in @($geonServer, $appServer)) { if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id } }
    }
}
