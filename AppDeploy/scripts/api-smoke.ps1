param(
    [string]$BaseUrl = "http://localhost:8080",
    [string]$OutputDir = "",
    [switch]$IncludeGpu
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$RequestDir = Join-Path $Root "examples\requests"
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
            "X-Request-ID" = "req-smoke-$RunId"
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

function Assert-Equal {
    param(
        [string]$Name,
        [object]$Actual,
        [object]$Expected
    )
    if ($Actual -ne $Expected) {
        throw "$Name = $Actual, want $Expected"
    }
}

$health = Invoke-Api -Name "00-healthz" -Method "GET" -Path "/api/v1/healthz"
Assert-Equal -Name "healthz.status" -Actual $health.status -Expected "ok"

$readiness = Invoke-Api -Name "01-readiness" -Method "GET" -Path "/api/v1/readiness"
Assert-Equal -Name "readiness.status" -Actual $readiness.status -Expected "ready"

$cpuApp = Read-JsonFile "app-cpu-script.json"
$cpuApp.app_spec.metadata.name = "sample-cpu-app-$RunId"
$createdApp = Invoke-Api -Name "02-app-cpu" -Method "POST" -Path "/api/v1/apps" -Body $cpuApp
if ([string]::IsNullOrWhiteSpace($createdApp.app_version_id)) {
    throw "empty app_version_id"
}

$cpuRuntime = Read-JsonFile "runtime-cpu-vm.json"
Invoke-Api -Name "03-runtime-cpu" -Method "POST" -Path "/api/v1/runtime-profiles" -Body $cpuRuntime | Out-Null

$cpuTarget = Read-JsonFile "target-cpu-vm.json"
Invoke-Api -Name "04-target-cpu" -Method "POST" -Path "/api/v1/target-profiles" -Body $cpuTarget | Out-Null

$cpuCheck = Read-JsonFile "resource-check-cpu.json"
$resourceCheck = Invoke-Api -Name "05-resource-check-cpu" -Method "POST" -Path "/api/v1/resources/check" -Body $cpuCheck
Assert-Equal -Name "resource.status" -Actual $resourceCheck.status -Expected "available"

$deploymentBody = Read-JsonFile "deployment-cpu-template.json"
$deploymentBody.app_version_id = $createdApp.app_version_id
$deployment = Invoke-Api -Name "06-deployment-cpu" -Method "POST" -Path "/api/v1/deployments" -Body $deploymentBody
Assert-Equal -Name "deployment.status" -Actual $deployment.status -Expected "RUNNING"

$metricBody = Read-JsonFile "metric-cpu-sample.json"
$metric = Invoke-Api -Name "07-metric-cpu" -Method "POST" -Path "/api/v1/deployments/$($deployment.deployment_id)/metrics" -Body $metricBody
if ([string]::IsNullOrWhiteSpace($metric.metric_id)) {
    throw "empty metric_id"
}

$logs = Invoke-Api -Name "08-deployment-logs" -Method "GET" -Path "/api/v1/deployments/$($deployment.deployment_id)/logs"
if ($logs.items.Count -lt 1) {
    throw "deployment logs are empty"
}

Invoke-Api -Name "09-deployment-metrics" -Method "GET" -Path "/api/v1/deployments/$($deployment.deployment_id)/metrics" | Out-Null
Invoke-Api -Name "10-monitoring-summary" -Method "GET" -Path "/api/v1/monitoring/summary" | Out-Null
Invoke-Api -Name "11-monitoring-runtime-health" -Method "GET" -Path "/api/v1/monitoring/runtime-health" | Out-Null
Invoke-Api -Name "12-monitoring-alarms" -Method "GET" -Path "/api/v1/monitoring/alarms" | Out-Null
Invoke-Api -Name "13-monitoring-metrics" -Method "GET" -Path "/api/v1/monitoring/metrics" | Out-Null

$stopped = Invoke-Api -Name "14-stop-deployment" -Method "POST" -Path "/api/v1/deployments/$($deployment.deployment_id)/stop"
Assert-Equal -Name "stop.status" -Actual $stopped.status -Expected "STOPPED"

if ($IncludeGpu) {
    $gpuApp = Read-JsonFile "app-gpu-script.json"
    $gpuApp.app_spec.metadata.name = "sample-gpu-app-$RunId"
    $createdGpuApp = Invoke-Api -Name "15-app-gpu" -Method "POST" -Path "/api/v1/apps" -Body $gpuApp
    $gpuRuntime = Read-JsonFile "runtime-gpu-vm.json"
    Invoke-Api -Name "16-runtime-gpu" -Method "POST" -Path "/api/v1/runtime-profiles" -Body $gpuRuntime | Out-Null
    $gpuTarget = Read-JsonFile "target-gpu-vm.json"
    Invoke-Api -Name "17-target-gpu" -Method "POST" -Path "/api/v1/target-profiles" -Body $gpuTarget | Out-Null
    $gpuCheck = Read-JsonFile "resource-check-gpu.json"
    Invoke-Api -Name "18-resource-check-gpu" -Method "POST" -Path "/api/v1/resources/check" -Body $gpuCheck | Out-Null
    $gpuDeploymentBody = Read-JsonFile "deployment-gpu-template.json"
    $gpuDeploymentBody.app_version_id = $createdGpuApp.app_version_id
    $gpuDeployment = Invoke-Api -Name "19-deployment-gpu" -Method "POST" -Path "/api/v1/deployments" -Body $gpuDeploymentBody
    Assert-Equal -Name "gpu deployment.status" -Actual $gpuDeployment.status -Expected "RUNNING"
    Invoke-Api -Name "20-stop-gpu-deployment" -Method "POST" -Path "/api/v1/deployments/$($gpuDeployment.deployment_id)/stop" | Out-Null
}

Write-Output "api smoke ok: $Base"
