param(
    [string]$BaseUrl = "http://localhost:8080",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$RequestDir = Join-Path $Root "examples\interface\requests"
$Base = $BaseUrl.TrimEnd("/")
$RunId = Get-Date -Format "MMddHHmmss"

function Read-JsonFile {
    param([string]$Name)
    $path = Join-Path $RequestDir $Name
    return Get-Content -Raw -Path $path | ConvertFrom-Json
}

function Save-Json {
    param(
        [string]$Name,
        [object]$Data
    )
    if ([string]::IsNullOrWhiteSpace($OutputDir)) {
        return
    }
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
    $path = Join-Path $OutputDir "$Name.json"
    $Data | ConvertTo-Json -Depth 50 | Set-Content -Path $path -Encoding utf8
}

function Invoke-Api {
    param(
        [string]$Name,
        [string]$Method,
        [string]$Path,
        [object]$Body = $null
    )
    $params = @{
        Method = $Method
        Uri = "$Base$Path"
        Headers = @{
            "X-Request-ID" = "req-interface-$RunId"
            "Accept" = "application/json"
        }
    }
    if ($null -ne $Body) {
        $params["ContentType"] = "application/json"
        $params["Body"] = ($Body | ConvertTo-Json -Depth 50)
    }
    $response = Invoke-RestMethod @params
    Save-Json -Name $Name -Data $response
    return $response
}

function Invoke-ExpectedFailure {
    param(
        [string]$Name,
        [string]$Method,
        [string]$Path,
        [object]$Body
    )
    try {
        Invoke-Api -Name $Name -Method $Method -Path $Path -Body $Body | Out-Null
        throw "$Name unexpectedly succeeded"
    } catch {
        $response = $_.Exception.Response
        $raw = $_.ErrorDetails.Message
        if ([string]::IsNullOrWhiteSpace($raw) -and $null -ne $response) {
            $stream = $response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            $raw = $reader.ReadToEnd()
        }
        if ([string]::IsNullOrWhiteSpace($raw)) {
            throw
        }
        $data = $raw | ConvertFrom-Json
        Save-Json -Name $Name -Data $data
        return $data
    }
}

function Assert-NotEmpty {
    param(
        [string]$Name,
        [object]$Value
    )
    if ([string]::IsNullOrWhiteSpace([string]$Value)) {
        throw "$Name is empty"
    }
}

$health = Invoke-Api -Name "00-healthz" -Method "GET" -Path "/api/v1/healthz"
Assert-NotEmpty -Name "healthz.request_id" -Value $health.request_id

$readiness = Invoke-Api -Name "01-readiness" -Method "GET" -Path "/api/v1/readiness"
Assert-NotEmpty -Name "readiness.request_id" -Value $readiness.request_id

$cpuApp = Read-JsonFile "app-create-cpu.json"
$cpuApp.metadata.name = "interface-cpu-$RunId"
$createdCpuApp = Invoke-Api -Name "02-app-cpu" -Method "POST" -Path "/api/v1/apps" -Body $cpuApp
Assert-NotEmpty -Name "cpu app_version_id" -Value $createdCpuApp.app_version_id

$gpuApp = Read-JsonFile "app-create-gpu.json"
$gpuApp.metadata.name = "interface-gpu-$RunId"
$createdGpuApp = Invoke-Api -Name "03-app-gpu" -Method "POST" -Path "/api/v1/apps" -Body $gpuApp
Assert-NotEmpty -Name "gpu app_version_id" -Value $createdGpuApp.app_version_id

$invalidApp = Read-JsonFile "app-create-invalid-container.json"
$invalidApp.metadata.name = "interface-invalid-$RunId"
$invalidResponse = Invoke-ExpectedFailure -Name "04-app-invalid-container" -Method "POST" -Path "/api/v1/apps" -Body $invalidApp
if ($invalidResponse.error.code -ne "APP_SPEC_INVALID") {
    throw "invalid app error code = $($invalidResponse.error.code), want APP_SPEC_INVALID"
}

$runtime = Read-JsonFile "runtime-profile-gpu-vm.json"
Invoke-Api -Name "05-runtime-gpu" -Method "POST" -Path "/api/v1/runtime-profiles" -Body $runtime | Out-Null

$target = Read-JsonFile "target-profile-aws-gpu.json"
Invoke-Api -Name "06-target-gpu" -Method "POST" -Path "/api/v1/target-profiles" -Body $target | Out-Null

$resource = Read-JsonFile "resource-check-gpu.json"
$resourceCheck = Invoke-Api -Name "07-resource-check-gpu" -Method "POST" -Path "/api/v1/resources/check" -Body $resource
Assert-NotEmpty -Name "resource.request_id" -Value $resourceCheck.request_id

$deploymentBody = Read-JsonFile "deployment-create-gpu.json"
$deploymentBody.app_id = $createdGpuApp.app_id
$deploymentBody.app_version_id = $createdGpuApp.app_version_id
$deployment = Invoke-Api -Name "08-deployment-gpu" -Method "POST" -Path "/api/v1/deployments" -Body $deploymentBody
Assert-NotEmpty -Name "deployment_id" -Value $deployment.deployment_id

$logs = Invoke-Api -Name "09-deployment-logs" -Method "GET" -Path "/api/v1/deployments/$($deployment.deployment_id)/logs"
if (($logs.items.Count -lt 1) -and ($logs.logs.Count -lt 1)) {
    throw "deployment logs are empty"
}

$metricBody = @{
    latency_ms = 12.5
    throughput_rps = 4.5
    quality_score = 0.91
    request_count = 20
    error_count = 0
}
$metric = Invoke-Api -Name "10-metric" -Method "POST" -Path "/api/v1/deployments/$($deployment.deployment_id)/metrics" -Body $metricBody
Assert-NotEmpty -Name "metric.request_id" -Value $metric.request_id

Invoke-Api -Name "11-monitoring-summary" -Method "GET" -Path "/api/v1/monitoring/summary" | Out-Null
Invoke-Api -Name "12-monitoring-runtime-health" -Method "GET" -Path "/api/v1/monitoring/runtime-health" | Out-Null
Invoke-Api -Name "13-monitoring-alarms" -Method "GET" -Path "/api/v1/monitoring/alarms" | Out-Null
Invoke-Api -Name "14-monitoring-metrics" -Method "GET" -Path "/api/v1/monitoring/metrics" | Out-Null

$stopBody = Read-JsonFile "deployment-stop.json"
$stopped = Invoke-Api -Name "15-stop-deployment" -Method "POST" -Path "/api/v1/deployments/$($deployment.deployment_id)/stop" -Body $stopBody
Assert-NotEmpty -Name "stop.request_id" -Value $stopped.request_id

Write-Output "interface smoke ok: $Base"
