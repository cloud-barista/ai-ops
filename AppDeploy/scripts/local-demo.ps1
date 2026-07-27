$ErrorActionPreference = "Stop"
$moduleRoot = Split-Path -Parent $PSScriptRoot
Set-Location $moduleRoot

$env:RESOURCE_PROVIDER = "local"
$env:PLACEMENT_PROVIDER = "local"
$env:AIAPP_SERVER_PORT = "18080"
$storePath = Join-Path $moduleRoot "tmp\local-demo-store.json"
if (Test-Path -LiteralPath $storePath) { Remove-Item -LiteralPath $storePath -Force }
$env:AIAPP_STORE_PATH = $storePath
$env:AIAPP_LOCAL_RUNTIME_WORK_DIR = ".\tmp\local-runtime"
$env:GOCACHE = Join-Path $moduleRoot ".gocache"
$logPath = Join-Path $moduleRoot "tmp\local-demo-server.log"
$errorPath = Join-Path $moduleRoot "tmp\local-demo-server.err.log"
$binaryPath = Join-Path $moduleRoot "tmp\local-demo-server.exe"
New-Item -ItemType Directory -Force (Split-Path $logPath) | Out-Null

go build -o $binaryPath ./cmd/server
if ($LASTEXITCODE -ne 0) { throw "could not build local demo server" }

$startInfo = [System.Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = $binaryPath
$startInfo.WorkingDirectory = $moduleRoot
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$server = [System.Diagnostics.Process]::Start($startInfo)
$stdout = $server.StandardOutput
$stderr = $server.StandardError
$stdoutTask = $stdout.ReadToEndAsync()
$stderrTask = $stderr.ReadToEndAsync()
try {
    $ready = $false
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            $health = Invoke-RestMethod "http://localhost:18080/api/v1/healthz"
            if ($health.status -eq "ok") { $ready = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw "server did not become ready; see $errorPath" }

    $appDir = Join-Path $moduleRoot "examples\local-app"
    $artifactURI = ([Uri]$appDir).AbsoluteUri
    $app = @{
        app_spec = @{
            schema_version = "appspec.khu.ai/v1alpha1"; kind = "AIApp"
            metadata = @{ name = "local-worker"; version = "0.1.0" }
            artifact = @{ type = "script"; uri = $artifactURI }
            entrypoint = @{ command = "powershell.exe"; args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "run.ps1") }
            runtime = @{ type = "cpu" }
            resources = @{ cpu = "1"; memory = "256Mi"; storage = "1Gi" }
        }
    } | ConvertTo-Json -Depth 10
    $registered = Invoke-RestMethod "http://localhost:18080/api/v1/apps" -Method Post -ContentType "application/json" -Body $app

    $node = @{
        target_profile_id = "local-node-cheap"; vm_type = "Local"; csp = "local"; status = "READY"; cost_weight = 1
        vm = @{}; runtime = @{ runtime_type = "local"; operating_mode = "local_process" }
        supported_runtimes = @("cpu"); labels = @{ zone = "demo" }
        capacity = @{ cpu_cores = 2; memory_bytes = 4294967296; storage_bytes = 10737418240 }
        storage = @{ artifact_dir = $appDir; log_dir = (Join-Path $moduleRoot "tmp\local-runtime-logs") }
    } | ConvertTo-Json -Depth 10
    Invoke-RestMethod "http://localhost:18080/api/v1/target-profiles" -Method Post -ContentType "application/json" -Body $node | Out-Null

    $deployment = @{
        app_version_id = $registered.app_version_id; requested_by = "local-demo"
        requirements = @{ resources = @{ cpu = "1"; memory = "256Mi"; storage = "1Gi" }; runtime = "cpu"; cost_policy = "min_cost"; labels = @{ zone = "demo" } }
    } | ConvertTo-Json -Depth 10
    $created = Invoke-RestMethod "http://localhost:18080/api/v1/deployments" -Method Post -ContentType "application/json" -Body $deployment
    Write-Host ("deployment_id: " + $created.deployment_id)
    Write-Host ("selected_vm: " + $created.placement.target_vm_id)
    Write-Host ("reason: " + $created.placement.reason)
    Start-Sleep -Seconds 1
    $logs = Invoke-RestMethod ("http://localhost:18080/api/v1/deployments/{0}/logs" -f $created.deployment_id)
    $logs.items | ForEach-Object { Write-Host ("[{0}] {1}" -f $_.stage, $_.message) }
    Invoke-RestMethod ("http://localhost:18080/api/v1/deployments/{0}/stop" -f $created.deployment_id) -Method Post | Out-Null
    Start-Sleep -Milliseconds 300
    $runtimeLogs = Invoke-RestMethod ("http://localhost:18080/api/v1/deployments/{0}/logs" -f $created.deployment_id)
    $runtimeLogs.items | Where-Object { $_.component -eq "local-process-adapter" } | ForEach-Object { Write-Host ("[runtime:{0}] {1}" -f $_.stage, $_.message) }
    Write-Host "local deployment stopped and reservation released"
} finally {
    if ($server -and -not $server.HasExited) { $server.Kill() }
    if ($server) {
        $server.WaitForExit(5000) | Out-Null
        [System.IO.File]::WriteAllText($logPath, $stdoutTask.Result)
        [System.IO.File]::WriteAllText($errorPath, $stderrTask.Result)
    }
    if (Test-Path -LiteralPath $binaryPath) {
        Start-Sleep -Milliseconds 200
        Remove-Item -LiteralPath $binaryPath -Force -ErrorAction SilentlyContinue
    }
}
