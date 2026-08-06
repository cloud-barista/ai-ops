param(
    [switch]$DisableOptimizer
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$moduleRoot = Join-Path $repoRoot "go\service-control-api"
$workspaceRoot = Split-Path -Parent $repoRoot
$appDeployRoot = Join-Path $workspaceRoot "AppDeploy"
$appDeployPort = 18089
$controlPort = 18189
$appDeployBaseURL = "http://127.0.0.1:$appDeployPort/api/v1"
$controlBaseURL = "http://127.0.0.1:$controlPort/api/v1"
$appDeployStore = Join-Path $appDeployRoot "tmp\fault-recovery-loop-store.json"
$appDeployRuntime = Join-Path $appDeployRoot "tmp\fault-recovery-loop-runtime"
$appDeployBinary = Join-Path $appDeployRoot "tmp\fault-recovery-loop-appdeploy.exe"
$controlBinary = Join-Path $moduleRoot "tmp\fault-recovery-loop-control.exe"
$appDir = Join-Path $appDeployRoot "examples\local-app"
$appDeployProcess = $null
$controlProcess = $null

function Start-LocalProcess([string]$file, [string]$workingDirectory, [hashtable]$environment) {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $file
    $startInfo.WorkingDirectory = $workingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $previous = @{}
    foreach ($entry in $environment.GetEnumerator()) {
        $previous[$entry.Key] = [Environment]::GetEnvironmentVariable($entry.Key, "Process")
        [Environment]::SetEnvironmentVariable($entry.Key, [string]$entry.Value, "Process")
    }
    try {
        return [System.Diagnostics.Process]::Start($startInfo)
    } finally {
        foreach ($entry in $environment.GetEnumerator()) {
            [Environment]::SetEnvironmentVariable($entry.Key, $previous[$entry.Key], "Process")
        }
    }
}

function Wait-Ready([string]$url, [System.Diagnostics.Process]$process) {
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        if ($process.HasExited) {
            throw "service exited before readiness: $url"
        }
        try {
            $response = Invoke-RestMethod -Uri $url -TimeoutSec 2
            if ($response.status -eq "ok") {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    throw "service did not become ready: $url"
}

function Invoke-JsonRequest([string]$uri, [string]$method, $body) {
    $jsonBody = $null
    if ($null -ne $body) {
        $jsonBody = $body | ConvertTo-Json -Depth 20 -Compress
    }
    try {
        $response = Invoke-WebRequest -Uri $uri -Method $method -ContentType "application/json" -Body $jsonBody -UseBasicParsing
        $raw = $response.Content
        return [pscustomobject]@{
            StatusCode = [int]$response.StatusCode
            Json       = if ([string]::IsNullOrWhiteSpace($raw)) { $null } else { $raw | ConvertFrom-Json }
            Raw        = $raw
        }
    } catch {
        $webResponse = $_.Exception.Response
        if ($null -eq $webResponse) {
            throw
        }
        $raw = $_.ErrorDetails.Message
        if ([string]::IsNullOrWhiteSpace($raw)) {
            $reader = [System.IO.StreamReader]::new($webResponse.GetResponseStream())
            try {
                $raw = $reader.ReadToEnd()
            } finally {
                $reader.Dispose()
            }
        }
        return [pscustomobject]@{
            StatusCode = [int]$webResponse.StatusCode
            Json       = if ([string]::IsNullOrWhiteSpace($raw)) { $null } else { $raw | ConvertFrom-Json }
            Raw        = $raw
        }
    }
}

function New-Envelope([string]$messageID, [string]$messageType, [string]$correlationID, [string]$traceID, [hashtable]$data) {
    return @{
        contract_version = "1.0"
        message_id       = $messageID
        message_type     = $messageType
        occurred_at      = (Get-Date).ToUniversalTime().ToString("o")
        correlation_id   = $correlationID
        trace_id         = $traceID
        source           = @{ system = "appdeploy"; component = "local-runtime" }
        target           = @{ system = "khu-geon"; component = "agent-control" }
        data             = $data
    }
}

function Send-FailureFeedback(
    [string]$correlationID,
    [string]$traceID,
    [string]$decisionID,
    [string]$deploymentID,
    [string]$errorCode,
    [string]$message,
    [int]$attempt
) {
    $now = (Get-Date).ToUniversalTime().ToString("o")
    $status = New-Envelope "fault-loop-status-$attempt" "deployment.status.changed" $correlationID $traceID @{
        deployment_status = @{
            deployment_id = $deploymentID
            decision_id   = $decisionID
            state         = "FAILED"
            error_code    = $errorCode
            message       = $message
            updated_at    = $now
        }
    }
    $statusResult = Invoke-JsonRequest "$controlBaseURL/agent-control/deployment-status" "Post" $status
    if ($statusResult.StatusCode -ne 202) {
        throw "Agent Control rejected deployment status: $($statusResult.StatusCode) $($statusResult.Raw)"
    }

    $feedback = New-Envelope "fault-loop-feedback-$attempt" "optimization.feedback.created" $correlationID $traceID @{
        optimization_feedback = @{
            feedback_id         = "fault-loop-feedback-$attempt"
            decision_id          = $decisionID
            deployment_id        = $deploymentID
            outcome              = "FAILED"
            error_code           = $errorCode
            observation_window   = @{ started_at = $now; ended_at = $now }
            metrics              = @{
                resource  = @{ cpu_average_percent = 0; memory_peak_mib = 0; accelerator_average_percent = 0; accelerator_memory_peak_mib = 0 }
                inference = @{ latency_p95_ms = 0; throughput_rps = 0; error_rate_percent = 100 }
                cost      = @{ currency = "LOCAL"; estimated_cost = 1 }
            }
            slo_violations       = @("deployment_failed")
            created_at           = $now
        }
    }
    $feedbackResult = Invoke-JsonRequest "$controlBaseURL/agent-control/optimization-feedback" "Post" $feedback
    if ($feedbackResult.StatusCode -ne 202) {
        throw "Agent Control rejected optimization feedback: $($feedbackResult.StatusCode) $($feedbackResult.Raw)"
    }
    if ($null -eq $feedbackResult.Json.repair_decision) {
        throw "Agent Control did not return RepairDecision: $($feedbackResult.Raw)"
    }
    return $feedbackResult.Json.repair_decision
}

function Send-SuccessFeedback(
    [string]$correlationID,
    [string]$traceID,
    [string]$decisionID,
    [string]$deploymentID,
    [int]$attempt
) {
    $now = (Get-Date).ToUniversalTime().ToString("o")
    $status = New-Envelope "fault-loop-status-$attempt" "deployment.status.changed" $correlationID $traceID @{
        deployment_status = @{
            deployment_id = $deploymentID
            decision_id   = $decisionID
            state         = "RUNNING"
            message       = "local deployment recovered"
            updated_at    = $now
        }
    }
    $statusResult = Invoke-JsonRequest "$controlBaseURL/agent-control/deployment-status" "Post" $status
    if ($statusResult.StatusCode -ne 202) {
        throw "Agent Control rejected running status: $($statusResult.StatusCode) $($statusResult.Raw)"
    }
    $feedback = New-Envelope "fault-loop-feedback-$attempt" "optimization.feedback.created" $correlationID $traceID @{
        optimization_feedback = @{
            feedback_id       = "fault-loop-feedback-$attempt"
            decision_id        = $decisionID
            deployment_id      = $deploymentID
            outcome            = "SUCCEEDED"
            observation_window = @{ started_at = $now; ended_at = $now }
            metrics            = @{
                resource  = @{ cpu_average_percent = 10; memory_peak_mib = 256; accelerator_average_percent = 0; accelerator_memory_peak_mib = 0 }
                inference = @{ latency_p95_ms = 100; throughput_rps = 1; error_rate_percent = 0 }
                cost      = @{ currency = "LOCAL"; estimated_cost = 1 }
            }
            created_at = $now
        }
    }
    $feedbackResult = Invoke-JsonRequest "$controlBaseURL/agent-control/optimization-feedback" "Post" $feedback
    if ($feedbackResult.StatusCode -ne 202) {
        throw "Agent Control rejected success feedback: $($feedbackResult.StatusCode) $($feedbackResult.Raw)"
    }
}

function Start-AutomationFlow([string]$memory, [int]$attempt) {
    $memoryMiB = if ($memory -eq "512Mi") { 512 } else { 256 }
    $runResult = Invoke-JsonRequest "$controlBaseURL/agent-control/automation-runs" "Post" @{
        input_type = "structured"
        requested_by = "fault-recovery-closed-loop"
        app_spec = @{
            app_id = "fault-recovery-loop-app"
            app_version = "0.1.0"
            workload_type = "INFERENCE"
            cpu_cores = 1
            memory_mib = $memoryMiB
            storage_gib = 1
            replicas_min = 1
            replicas_max = 1
            expected_rps = 1
        }
    }
    if ($runResult.StatusCode -ne 201 -or $null -eq $runResult.Json.Flow.Decision) {
        throw "Agent Control automation flow failed for attempt $attempt`: $($runResult.Raw)"
    }
    return $runResult.Json
}

try {
    New-Item -ItemType Directory -Force (Join-Path $appDeployRoot "tmp") | Out-Null
    New-Item -ItemType Directory -Force (Join-Path $moduleRoot "tmp") | Out-Null
    if (Test-Path -LiteralPath $appDeployStore) { Remove-Item -LiteralPath $appDeployStore -Force }

    $env:GOTELEMETRY = "off"
    $env:GOCACHE = Join-Path $moduleRoot "tmp\fault-recovery-loop-gocache"

    Push-Location $appDeployRoot
    go build -o $appDeployBinary ./cmd/server
    if ($LASTEXITCODE -ne 0) { throw "could not build AppDeploy server" }
    Pop-Location

    Push-Location $moduleRoot
    go build -o $controlBinary ./cmd/service-control-api
    if ($LASTEXITCODE -ne 0) { throw "could not build service-control-api" }
    Pop-Location

    $appDeployProcess = Start-LocalProcess $appDeployBinary $appDeployRoot @{
        RESOURCE_PROVIDER                  = "local"
        PLACEMENT_PROVIDER                 = "local"
        AIAPP_SERVER_PORT                  = "$appDeployPort"
        AIAPP_STORE_PATH                   = $appDeployStore
        AIAPP_LOCAL_RUNTIME_WORK_DIR       = $appDeployRuntime
        AIAPP_LOCAL_FAULT_RATE             = "1.0"
        AIAPP_LOCAL_FAULT_SEED             = "42"
        AIAPP_LOCAL_FAULT_MAX              = "8"
        AIAPP_LOCAL_FAULT_CODES            = "GPU_OOM,CUDA_MISMATCH,RESOURCE_UNAVAILABLE,TRANSIENT_DEPLOYMENT_FAILURE"
    }
    if (-not $DisableOptimizer) {
        $controlProcess = Start-LocalProcess $controlBinary $moduleRoot @{ PORT = "$controlPort" }
    }
    Wait-Ready "http://127.0.0.1:$appDeployPort/api/v1/healthz" $appDeployProcess
    if (-not $DisableOptimizer) {
        Wait-Ready "http://127.0.0.1:$controlPort/healthz" $controlProcess
    }

    $artifactURI = ([Uri]$appDir).AbsoluteUri
    $appResult = Invoke-JsonRequest "$appDeployBaseURL/apps" "Post" @{
        app_spec = @{
            schema_version = "appspec.khu.ai/v1alpha1"
            kind = "AIApp"
            metadata = @{ name = "fault-recovery-loop-app"; version = "0.1.0" }
            artifact = @{ type = "script"; uri = $artifactURI }
            entrypoint = @{ command = "powershell.exe"; args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "run.ps1") }
            runtime = @{ type = "cpu" }
            resources = @{ cpu = "1"; memory = "256Mi"; storage = "1Gi" }
        }
    }
    if ($appResult.StatusCode -ne 201) { throw "AppDeploy app registration failed: $($appResult.Raw)" }
    $app = $appResult.Json

    foreach ($targetID in @("fault-loop-primary", "fault-loop-alternative")) {
        $targetResult = Invoke-JsonRequest "$appDeployBaseURL/target-profiles" "Post" @{
            target_profile_id = $targetID
            vm_type = "Local"
            csp = "local"
            status = "READY"
            runtime = @{ runtime_type = "local"; operating_mode = "local_process" }
            supported_runtimes = @("cpu")
            capacity = @{ cpu_cores = 2; memory_bytes = 4294967296; storage_bytes = 10737418240 }
        }
        if ($targetResult.StatusCode -ne 201) { throw "target registration failed: $($targetResult.Raw)" }
    }

    if (-not $DisableOptimizer) {
        $run = Start-AutomationFlow "256Mi" 1
        $correlationID = $run.correlation_id
        $traceID = $run.trace_id
        $decisionID = $run.flow.decision.decision_id
    }

    $targetID = "fault-loop-primary"
    $memory = "256Mi"
    $faultCount = 0
    $successCount = 0
    $estimatedCost = 0.0
    $blindRetryAttempts = 0
    $repairActionAttempts = 0
    $targetChanges = 0
    $actionCounts = @{}
    $maxAttempts = 16
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        $estimatedCost += if ($targetID -eq "fault-loop-alternative") {
            if ($memory -eq "512Mi") { 1.2 } else { 1.0 }
        } else {
            2.0
        }
        $deploymentResult = Invoke-JsonRequest "$appDeployBaseURL/deployments" "Post" @{
            app_version_id = $app.app_version_id
            target_profile_id = $targetID
            requirements = @{
                runtime = "cpu"
                resources = @{ cpu = "1"; memory = $memory; storage = "1Gi" }
            }
        }

        if ($deploymentResult.StatusCode -ge 200 -and $deploymentResult.StatusCode -lt 300) {
            $deployment = $deploymentResult.Json
            if ($deployment.status -ne "RUNNING") { throw "recovery deployment did not run: $($deploymentResult.Raw)" }
            if ($deployment.target_profile_id -ne $targetID) { throw "target action was not applied: expected=$targetID actual=$($deployment.target_profile_id)" }
            if (-not $DisableOptimizer) {
                Send-SuccessFeedback $correlationID $traceID $decisionID $deployment.deployment_id $attempt
            }
            $stopResult = Invoke-JsonRequest "$appDeployBaseURL/deployments/$($deployment.deployment_id)/stop" "Post" $null
            if ($stopResult.StatusCode -lt 200 -or $stopResult.StatusCode -ge 300) { throw "recovered deployment stop failed: $($stopResult.Raw)" }
            $successCount++
            Write-Host ("closed_loop: recovered attempt={0} target={1} memory={2}" -f $attempt, $targetID, $memory)
            break
        }

        $faultCount++
        $faultError = $deploymentResult.Json.error
        $details = $faultError.details
        $deploymentID = [string]$details.deployment_id
        if ([string]::IsNullOrWhiteSpace($deploymentID)) { throw "fault response did not contain deployment_id: $($deploymentResult.Raw)" }
		if ($DisableOptimizer) {
			$blindRetryAttempts++
			Write-Host ("closed_loop: blind fault attempt={0} code={1} action=RETRY_SAME_TARGET" -f $attempt, $faultError.code)
			continue
		}
        $repair = Send-FailureFeedback $correlationID $traceID $decisionID $deploymentID ([string]$faultError.code) ([string]$faultError.message) $attempt
        $action = [string]$repair.action
        $repairActionAttempts++
        if (-not $actionCounts.ContainsKey($action)) { $actionCounts[$action] = 0 }
        $actionCounts[$action]++
        Write-Host ("closed_loop: fault attempt={0} code={1} action={2}" -f $attempt, $faultError.code, $action)
        switch ($action) {
            "RETRY_DEPLOYMENT" { }
            "REQUEST_ALTERNATIVE_RESOURCE" {
                if ($targetID -ne "fault-loop-alternative") { $targetChanges++ }
                $targetID = "fault-loop-alternative"
            }
            "ADJUST_RESOURCE_REQUIREMENT" {
                if ($targetID -ne "fault-loop-alternative") { $targetChanges++ }
                $targetID = "fault-loop-alternative"
                $memory = "512Mi"
            }
            default { throw "closed loop cannot execute RepairDecision action: $action" }
        }
		$nextRun = Start-AutomationFlow $memory ($attempt + 1)
		$correlationID = $nextRun.correlation_id
		$traceID = $nextRun.trace_id
		$decisionID = $nextRun.flow.decision.decision_id
    }

    if ($successCount -ne 1 -or $faultCount -eq 0) {
        throw "closed loop did not recover: faults=$faultCount successes=$successCount actions=$($actionCounts | Out-String)"
    }
    $mode = if ($DisableOptimizer) { "without_optimizer" } else { "with_optimizer" }
    $result = [ordered]@{
        mode                   = $mode
        faults                 = $faultCount
        attempts               = $faultCount + $successCount
        successes              = $successCount
        estimated_cost         = $estimatedCost
        blind_retry_attempts   = $blindRetryAttempts
        repair_action_attempts = $repairActionAttempts
        target_changes         = $targetChanges
        actions                = $actionCounts
    }
    Write-Host ("fault_recovery_closed_loop: PASS " + ($result | ConvertTo-Json -Compress))
} finally {
    if ($controlProcess -and -not $controlProcess.HasExited) {
        $controlProcess.Kill()
        $controlProcess.WaitForExit(5000) | Out-Null
    }
    if ($appDeployProcess -and -not $appDeployProcess.HasExited) {
        $appDeployProcess.Kill()
        $appDeployProcess.WaitForExit(5000) | Out-Null
    }
    Pop-Location -ErrorAction SilentlyContinue
    foreach ($path in @($appDeployBinary, $controlBinary, $appDeployStore)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue }
    }
    foreach ($path in @($appDeployRuntime)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue }
    }
}
