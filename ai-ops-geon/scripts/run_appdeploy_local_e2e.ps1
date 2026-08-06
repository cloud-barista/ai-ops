$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $repoRoot
$appDeployRoot = Join-Path $workspaceRoot "AppDeploy"
$appDeployPort = 18088
$llmPort = 18100
$appDeployBaseURL = "http://127.0.0.1:$appDeployPort/api/v1"
$appDeployStore = Join-Path $appDeployRoot "tmp\optimizer-e2e-store.json"
$appDeployRuntimeDir = Join-Path $appDeployRoot "tmp\optimizer-e2e-runtime"
$appDeployRuntimeLogs = Join-Path $appDeployRoot "tmp\optimizer-e2e-runtime-logs"
$appDeployBinary = Join-Path $appDeployRoot "tmp\optimizer-e2e-server.exe"
$cliBinary = Join-Path $repoRoot "tmp\optimizer-e2e-cli.exe"
$candidateConfig = Join-Path $repoRoot "tmp\optimizer-e2e-candidates.json"
$appDir = Join-Path $appDeployRoot "examples\local-app"
$appDeployServer = $null
$llmProcess = $null

New-Item -ItemType Directory -Force (Join-Path $appDeployRoot "tmp") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $repoRoot "tmp") | Out-Null
if (Test-Path -LiteralPath $appDeployStore) { Remove-Item -LiteralPath $appDeployStore -Force }

function Wait-HttpReady([string]$url) {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri $url -TimeoutSec 2
            if ($health.status -eq "ok") { return }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    throw "service did not become ready: $url"
}

try {
    $env:GOTELEMETRY = "off"
    $env:GOCACHE = Join-Path $repoRoot "tmp\optimizer-e2e-gocache"

    Push-Location $appDeployRoot
    $env:RESOURCE_PROVIDER = "local"
    $env:PLACEMENT_PROVIDER = "local"
    $env:AIAPP_SERVER_PORT = "$appDeployPort"
    $env:AIAPP_STORE_PATH = $appDeployStore
    $env:AIAPP_LOCAL_RUNTIME_WORK_DIR = ".\tmp\optimizer-e2e-runtime"
    go build -o $appDeployBinary ./cmd/server
    if ($LASTEXITCODE -ne 0) { throw "could not build AppDeploy local server" }

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $appDeployBinary
    $startInfo.WorkingDirectory = $appDeployRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $appDeployServer = [System.Diagnostics.Process]::Start($startInfo)
    Pop-Location

    Wait-HttpReady "http://127.0.0.1:$appDeployPort/api/v1/healthz"

    $artifactURI = ([Uri]$appDir).AbsoluteUri
    $appBody = @{
        app_spec = @{
            schema_version = "appspec.khu.ai/v1alpha1"; kind = "AIApp"
            metadata = @{ name = "optimizer-e2e-worker"; version = "0.1.0" }
            artifact = @{ type = "script"; uri = $artifactURI }
            entrypoint = @{ command = "powershell.exe"; args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "run.ps1") }
            runtime = @{ type = "cpu" }
            resources = @{ cpu = "1"; memory = "256Mi"; storage = "1Gi" }
        }
    } | ConvertTo-Json -Depth 10
    $registered = Invoke-RestMethod "$appDeployBaseURL/apps" -Method Post -ContentType "application/json" -Body $appBody

    $targetBody = @{
        target_profile_id = "local-node-optimizer-e2e"; vm_type = "Local"; csp = "local"; status = "READY"; cost_weight = 1
        vm = @{}; runtime = @{ runtime_type = "local"; operating_mode = "local_process" }
        supported_runtimes = @("cpu"); labels = @{ zone = "optimizer-e2e" }
        capacity = @{ cpu_cores = 2; memory_bytes = 4294967296; storage_bytes = 10737418240 }
        storage = @{ artifact_dir = $appDir; log_dir = $appDeployRuntimeLogs }
    } | ConvertTo-Json -Depth 10
    Invoke-RestMethod "$appDeployBaseURL/target-profiles" -Method Post -ContentType "application/json" -Body $targetBody | Out-Null

    $llmStartInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $llmStartInfo.FileName = "powershell.exe"
    $llmStartInfo.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $PSScriptRoot 'run_deterministic_llm.ps1')`" -Port $llmPort"
    $llmStartInfo.WorkingDirectory = $repoRoot
    $llmStartInfo.UseShellExecute = $false
    $llmStartInfo.CreateNoWindow = $true
    $llmProcess = [System.Diagnostics.Process]::Start($llmStartInfo)
    Start-Sleep -Milliseconds 500
    if ($llmProcess.HasExited) {
        throw "local deterministic LLM endpoint did not start"
    }

    $candidateBody = @{
        version = "1"; benchmark_mode = "local_e2e"; benchmark_status = "executed"
        candidates = @(@{
            candidate_id = "optimizer-e2e-local"
            role_label = "local-e2e"
            provider = "local-deterministic"
            actual_model = "deployment-manifest-fixture"
            endpoint = "http://127.0.0.1:$llmPort/v1/chat/completions"
            enabled = $true; json_mode = $true; timeout_seconds = 10
        })
    } | ConvertTo-Json -Depth 10
    $utf8NoBOM = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($candidateConfig, $candidateBody, $utf8NoBOM)

    Push-Location (Join-Path $repoRoot "go\service-control-api")
    go build -o $cliBinary ./cmd/aiops-service-control
    if ($LASTEXITCODE -ne 0) { throw "could not build ai-ops-geon CLI" }
    $output = & $cliBinary run-appdeploy-planner `
        --request "CPU 1개, 메모리 256Mi, 디스크 1Gi가 필요한 추론 앱을 배포해 주세요." `
        --app-version-id $registered.app_version_id `
        --candidate-id "optimizer-e2e-local" `
        --candidates $candidateConfig `
        --guard-policy (Join-Path $repoRoot "config\planner_guard_policy.json") `
        --appdeploy-base-url $appDeployBaseURL `
        --requested-by "ai-ops-geon-planner" `
        --poll-interval-ms 100 `
        --max-poll-attempts 60 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    Pop-Location
    if ($exitCode -ne 0) { throw "ai-ops-geon AppDeploy E2E failed: $output" }
    $result = $output | ConvertFrom-Json
    if (-not $result.valid -or $result.status -ne "RUNNING") {
        throw "unexpected planner result: $output"
    }
    if ($result.deployment.status -ne "RUNNING" -or $result.deployment.target_profile_id -ne "local-node-optimizer-e2e") {
        throw "AppDeploy did not run on the expected local target: $output"
    }

    Write-Host "optimizer_appdeploy_e2e: PASS"
    Write-Host ("run_id: " + $result.run_id)
    Write-Host ("deployment_id: " + $result.deployment.deployment_id)
    Write-Host ("status: " + $result.status)
    Write-Host ("target_profile_id: " + $result.deployment.target_profile_id)
    Write-Host ("poll_attempts: " + $result.polling.attempts)
    Invoke-RestMethod "$appDeployBaseURL/deployments/$($result.deployment.deployment_id)/stop" -Method Post | Out-Null
} finally {
    if ($llmProcess -and -not $llmProcess.HasExited) {
        $llmProcess.Kill()
        $llmProcess.WaitForExit(5000) | Out-Null
    }
    if ($appDeployServer -and -not $appDeployServer.HasExited) {
        $appDeployServer.Kill()
        $appDeployServer.WaitForExit(5000) | Out-Null
    }
    Pop-Location -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $appDeployBinary) { Remove-Item -LiteralPath $appDeployBinary -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $cliBinary) { Remove-Item -LiteralPath $cliBinary -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $candidateConfig) { Remove-Item -LiteralPath $candidateConfig -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $appDeployStore) { Remove-Item -LiteralPath $appDeployStore -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $appDeployRuntimeDir) { Remove-Item -LiteralPath $appDeployRuntimeDir -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $appDeployRuntimeLogs) { Remove-Item -LiteralPath $appDeployRuntimeLogs -Recurse -Force -ErrorAction SilentlyContinue }
}
