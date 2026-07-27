param(
    [int]$Port = 18080,
    [string]$OutputRoot = "deliverables\evidence\results"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$RunId = Get-Date -Format "yyyyMMdd-HHmmss"
$RunDir = Join-Path (Join-Path $Root $OutputRoot) $RunId
$BaseUrl = "http://localhost:$Port"
$ServerOut = Join-Path $RunDir "server.stdout.txt"
$ServerErr = Join-Path $RunDir "server.stderr.txt"
$StorePath = Join-Path $RunDir "aiapp-store.json"
$SmokeOutDir = Join-Path $RunDir "api-smoke"
$BinaryPath = Join-Path $RunDir "appdeployer-server.exe"

New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

function Invoke-GoCheck {
    param(
        [string[]]$Arguments,
        [string]$OutputPath
    )

    $errorPath = "$OutputPath.stderr"
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = "go.exe"
    $startInfo.Arguments = $Arguments -join " "
    $startInfo.WorkingDirectory = $Root
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($startInfo)
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $null = $process.WaitForExit()
    [System.IO.File]::WriteAllText($OutputPath, $stdoutTask.Result + $stderrTask.Result)
    return $process.ExitCode
}

$ExistingPortOwners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
if ($ExistingPortOwners.Count -gt 0) {
    throw "port $Port is already in use by process id(s): $($ExistingPortOwners -join ', ')"
}

Push-Location $Root
try {
    $GoTestExit = Invoke-GoCheck @("test", "./...") (Join-Path $RunDir "go-test.txt")

    $GoVetExit = Invoke-GoCheck @("vet", "./...") (Join-Path $RunDir "go-vet.txt")

    $GoBuildExit = Invoke-GoCheck @("build", "-o", $BinaryPath, "./cmd/server") (Join-Path $RunDir "go-build.txt")
    if ($GoBuildExit -ne 0) {
        throw "server build failed; see $RunDir\go-build.txt"
    }

    $oldPort = $env:AIAPP_SERVER_PORT
    $oldStore = $env:AIAPP_STORE_PATH
    $oldCPU = $env:AIAPP_CPUVM_RUNNER
    $oldGPU = $env:AIAPP_GPUVM_RUNNER

    $env:AIAPP_SERVER_PORT = [string]$Port
    $env:AIAPP_STORE_PATH = $StorePath
    $env:AIAPP_CPUVM_RUNNER = "dry-run"
    $env:AIAPP_GPUVM_RUNNER = "dry-run"

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $BinaryPath
    $startInfo.WorkingDirectory = $Root
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $Server = [System.Diagnostics.Process]::Start($startInfo)
    $serverStdoutTask = $Server.StandardOutput.ReadToEndAsync()
    $serverStderrTask = $Server.StandardError.ReadToEndAsync()
    try {
        $ready = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Milliseconds 500
            try {
                $health = Invoke-RestMethod -Uri "$BaseUrl/api/v1/healthz" -Method GET
                if ($health.status -eq "ok") {
                    $ready = $true
                    break
                }
            } catch {
            }
        }
        if (-not $ready) {
            throw "server did not become ready on $BaseUrl"
        }

        try {
            & (Join-Path $Root "scripts\api-smoke.ps1") -BaseUrl $BaseUrl -OutputDir $SmokeOutDir *> (Join-Path $RunDir "api-smoke.txt")
            $SmokeExit = 0
        } catch {
            $_ | Out-String | Set-Content -Path (Join-Path $RunDir "api-smoke-error.txt") -Encoding utf8
            $SmokeExit = 1
        }

        Invoke-RestMethod -Uri "$BaseUrl/api/v1/readiness" -Method GET |
            ConvertTo-Json -Depth 20 |
            Set-Content -Path (Join-Path $RunDir "readiness.json") -Encoding utf8
        Invoke-RestMethod -Uri "$BaseUrl/api/v1/monitoring/summary" -Method GET |
            ConvertTo-Json -Depth 20 |
            Set-Content -Path (Join-Path $RunDir "monitoring-summary.json") -Encoding utf8
        Invoke-RestMethod -Uri "$BaseUrl/api/v1/monitoring/metrics" -Method GET |
            ConvertTo-Json -Depth 20 |
            Set-Content -Path (Join-Path $RunDir "monitoring-metrics.json") -Encoding utf8
    } finally {
        if ($null -ne $Server -and -not $Server.HasExited) {
            $Server.Kill()
        }
        if ($null -ne $Server) {
            $null = $Server.WaitForExit(5000)
            [System.IO.File]::WriteAllText($ServerOut, $serverStdoutTask.Result)
            [System.IO.File]::WriteAllText($ServerErr, $serverStderrTask.Result)
        }
        if (Test-Path -LiteralPath $BinaryPath) {
            Remove-Item -LiteralPath $BinaryPath -Force -ErrorAction SilentlyContinue
        }
        $PortOwners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
        foreach ($Owner in $PortOwners) {
            Stop-Process -Id $Owner -Force
        }
        $env:AIAPP_SERVER_PORT = $oldPort
        $env:AIAPP_STORE_PATH = $oldStore
        $env:AIAPP_CPUVM_RUNNER = $oldCPU
        $env:AIAPP_GPUVM_RUNNER = $oldGPU
    }

    [pscustomobject]@{
        generated_at = (Get-Date).ToUniversalTime().ToString("o")
        base_url = $BaseUrl
        output_dir = $RunDir
        go_test_exit_code = $GoTestExit
        go_vet_exit_code = $GoVetExit
        api_smoke_exit_code = $SmokeExit
        store_path = $StorePath
    } | ConvertTo-Json -Depth 10 | Set-Content -Path (Join-Path $RunDir "manifest.json") -Encoding utf8

    if ($GoTestExit -ne 0 -or $GoVetExit -ne 0 -or $SmokeExit -ne 0) {
        throw "evidence collection completed with failures; see $RunDir"
    }

    Write-Output "evidence ok: $RunDir"
} finally {
    Pop-Location
}
