# ai-ops-geon service-control API를 AppDeploy SSH runner로 배포/확인/중지하는 실행 스크립트.
# 사용자가 바꾸는 값은 conf/aiops-config-ssh.json에 두고, 이 파일은 실행 흐름만 담당한다.
param(
    [ValidateSet("deploy", "status", "stop", "package", "tunnel")]
    [string]$Action = "deploy",

    [string]$ConfigPath = "conf\aiops-config-ssh.json"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot

# AppDeploy 루트 기준 상대 경로를 절대 경로로 정규화한다.
function Resolve-PathFromRoot {
    param([string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }

    return [System.IO.Path]::GetFullPath((Join-Path $Root $Path))
}

# JSON 설정에 값이 없으면 스크립트 기본값을 사용한다.
function Get-Setting {
    param(
        $Object,
        [string]$Name,
        $Default
    )

    if ($null -eq $Object) {
        return $Default
    }

    $property = $Object.PSObject.Properties | Where-Object { $_.Name -eq $Name } | Select-Object -First 1
    if ($null -eq $property -or $null -eq $property.Value) {
        return $Default
    }

    return $property.Value
}

# ConvertFrom-Json 결과를 state 파일 갱신용 hashtable로 바꾼다.
function ConvertTo-Hashtable {
    param($Object)

    $table = @{}
    if ($null -eq $Object) {
        return $table
    }

    foreach ($property in $Object.PSObject.Properties) {
        $table[$property.Name] = $property.Value
    }

    return $table
}

# 설정 파일 로드. JSON은 주석 문법이 없으므로 설명은 _comment 필드로 둔다.
$ConfigFullPath = Resolve-PathFromRoot $ConfigPath
if (-not (Test-Path -LiteralPath $ConfigFullPath)) {
    throw "Config file not found: $ConfigFullPath"
}

$Config = Get-Content -LiteralPath $ConfigFullPath -Raw | ConvertFrom-Json

# 설정 파일을 기능별 섹션으로 나눠 읽는다.
$AppSettings = Get-Setting $Config "app" $null
$PackageSettings = Get-Setting $Config "package" $null
$AppDeploySettings = Get-Setting $Config "appdeploy" $null
$RuntimeProfileSettings = Get-Setting $Config "runtime_profile" $null
$TargetProfileSettings = Get-Setting $Config "target_profile" $null
$ServiceOperationSettings = Get-Setting $Config "service_operation" $null
$LoggingSettings = Get-Setting $Config "logging" $null
$StopSettings = Get-Setting $Config "stop" $null

# 자주 쓰는 실행 옵션은 변수로 뽑아 이후 함수들이 공통으로 사용한다.
$SshAlias = [string](Get-Setting $Config "ssh_alias" "nhn-cloud")
$AppDeployPort = [int](Get-Setting $Config "appdeploy_port" 18088)
$RemotePort = [int](Get-Setting $Config "remote_port" 18089)
$TunnelPort = [int](Get-Setting $Config "tunnel_port" 18189)
$AiOpsRoot = [string](Get-Setting $Config "aiops_root" "..\ai-ops-geon")
$RunRoot = [string](Get-Setting $Config "run_root" "tmp\aiops-geon-ssh-appdeploy-run")
$SkipTunnel = [bool](Get-Setting $Config "skip_tunnel" $false)
$ShowJsonResult = [bool](Get-Setting $LoggingSettings "show_json_result" $true)
$TailLogLines = [int](Get-Setting $LoggingSettings "tail_log_lines" 40)
$StopAllMatchingDeployments = [bool](Get-Setting $StopSettings "stop_all_matching_deployments" $true)
$StopTunnelOnStop = [bool](Get-Setting $StopSettings "stop_tunnel" $true)
$StopAppDeployMode = [string](Get-Setting $StopSettings "stop_appdeploy" "if_started_by_script")

# 작업 산출물 경로. 패키지, 상태 파일, stdout/stderr 로그가 모두 RunDir 아래에 모인다.
$RunDir = Resolve-PathFromRoot $RunRoot
$StoreFileName = [string](Get-Setting $AppDeploySettings "store_file" "aiapp-store.json")
$StorePath = Join-Path $RunDir $StoreFileName
$StatePath = Join-Path $RunDir "state.json"
$PackageRoot = Join-Path $RunDir "package"
$StageDir = Join-Path $PackageRoot "stage"
$ArchiveName = [string](Get-Setting $PackageSettings "archive_name" "aiops-geon-service-control-linux-amd64.tar.gz")
$ArchivePath = Join-Path $PackageRoot $ArchiveName
$BinaryName = [string](Get-Setting $PackageSettings "binary_name" "aiops-service-control-api")
$AppDeployStdout = Join-Path $RunDir "appdeploy.stdout.txt"
$AppDeployStderr = Join-Path $RunDir "appdeploy.stderr.txt"
$TunnelStdout = Join-Path $RunDir "tunnel.stdout.txt"
$TunnelStderr = Join-Path $RunDir "tunnel.stderr.txt"
$SshBatchArgs = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "LogLevel=ERROR")

# 터미널에서 실행 상황을 바로 볼 수 있도록 action/stage 기반 로그를 출력한다.
function Write-RunLog {
    param(
        [string]$Message,
        [string]$Stage = "main",
        [ValidateSet("INFO", "WARN", "ERROR")]
        [string]$Level = "INFO"
    )

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-Host "[$timestamp] [$Level] [$Action/$Stage] $Message"
}

# action 최종 결과를 JSON으로 출력한다. logging.show_json_result=false면 숨길 수 있다.
function Write-JsonResult {
    param($Data)

    if ($ShowJsonResult) {
        Write-RunLog -Stage "result" -Message "result JSON follows"
        $Data | ConvertTo-Json -Depth 20
    }
}

# 실패 시 AppDeploy/tunnel stderr의 마지막 일부를 보여줘서 원인을 바로 볼 수 있게 한다.
function Write-RecentFileLines {
    param(
        [string]$Path,
        [string]$Label
    )

    if ($TailLogLines -le 0 -or -not (Test-Path -LiteralPath $Path)) {
        return
    }

    Write-RunLog -Stage "logs" -Message "$Label tail ($Path)"
    Get-Content -LiteralPath $Path -Tail $TailLogLines | ForEach-Object {
        Write-Host "  $_"
    }
}

# SSH alias에서 host/user/port/key를 읽는다. 민감한 SSH 값은 JSON에 저장하지 않는다.
function Get-SshHostConfig {
    param([string]$Alias)

    $configPath = Join-Path $HOME ".ssh\config"
    if (-not (Test-Path -LiteralPath $configPath)) {
        throw "SSH config not found: $configPath"
    }

    $result = [ordered]@{
        Alias        = $Alias
        HostName     = $null
        User         = $null
        Port         = 22
        IdentityFile = $null
    }

    $inBlock = $false
    foreach ($rawLine in Get-Content -LiteralPath $configPath) {
        $line = $rawLine.Trim()
        if ($line.Length -eq 0 -or $line.StartsWith("#")) {
            continue
        }

        $parts = $line -split "\s+", 2
        if ($parts.Count -lt 2) {
            continue
        }

        $key = $parts[0].ToLowerInvariant()
        $value = $parts[1].Trim()

        if ($key -eq "host") {
            $patterns = $value -split "\s+"
            $inBlock = $patterns -contains $Alias
            continue
        }

        if (-not $inBlock) {
            continue
        }

        switch ($key) {
            "hostname" { $result.HostName = $value }
            "user" { $result.User = $value }
            "port" { $result.Port = [int]$value }
            "identityfile" {
                $expanded = $value.Trim('"').Trim("'").Replace("~", $HOME)
                $result.IdentityFile = [System.IO.Path]::GetFullPath($expanded)
            }
        }
    }

    if (-not $result.HostName) {
        throw "HostName for SSH alias '$Alias' was not found in $configPath"
    }
    if (-not $result.User) {
        throw "User for SSH alias '$Alias' was not found in $configPath"
    }
    if (-not $result.IdentityFile) {
        throw "IdentityFile for SSH alias '$Alias' was not found in $configPath"
    }
    if (-not (Test-Path -LiteralPath $result.IdentityFile)) {
        throw "IdentityFile for SSH alias '$Alias' does not exist: $($result.IdentityFile)"
    }

    return [pscustomobject]$result
}

# 원격 artifact/log 디렉터리를 HOME 아래에 만들기 위해 SSH로 원격 HOME을 확인한다.
function Get-RemoteHome {
    param([string]$Alias)

    Write-RunLog -Stage "ssh" -Message "resolving remote HOME through SSH alias '$Alias'"
    $homeText = (& ssh @SshBatchArgs $Alias 'printf %s "$HOME"') -join ""
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($homeText)) {
        throw "Failed to resolve remote HOME using SSH alias '$Alias'"
    }

    return $homeText.Trim()
}

function Convert-ToFileUri {
    param([string]$Path)
    return ([System.Uri]([System.IO.Path]::GetFullPath($Path))).AbsoluteUri
}

# credential_ref를 AppDeploy가 읽는 환경변수 prefix로 바꾼다.
function Convert-CredentialRefToEnvPrefix {
    param([string]$CredentialRef)

    $normalized = [regex]::Replace($CredentialRef, "[^A-Za-z0-9]+", "_").Trim("_").ToUpperInvariant()
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        throw "credential_ref cannot be empty"
    }

    return "AIAPP_CREDENTIAL_$normalized"
}

# 로컬 포트가 이미 사용 중인지 확인하고, 점유 프로세스 정보를 반환한다.
function Get-PortOwner {
    param([int]$Port)

    $connections = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($connections.Count -eq 0) {
        return $null
    }

    $ownerPid = $connections[0].OwningProcess
    $process = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
    return [pscustomobject]@{
        Port        = $Port
        OwningPid   = $ownerPid
        ProcessName = if ($process) { $process.ProcessName } else { $null }
    }
}

# HTTP endpoint가 준비될 때까지 짧게 polling한다.
function Wait-HttpOk {
    param(
        [string]$Uri,
        [int]$TimeoutSeconds = 30
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        try {
            return Invoke-RestMethod -Uri $Uri -Method Get -TimeoutSec 3
        } catch {
            Start-Sleep -Milliseconds 500
        }
    } while ((Get-Date) -lt $deadline)

    throw "Timed out waiting for $Uri"
}

# 마지막 실행 상태를 읽어 status/stop/tunnel action에서 재사용한다.
function Read-State {
    if (-not (Test-Path -LiteralPath $StatePath)) {
        return $null
    }

    return Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
}

# 마지막 deployment, tunnel pid, config path 등 후속 action에 필요한 상태를 저장한다.
function Write-State {
    param([hashtable]$State)

    New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
    $State["generated_at"] = (Get-Date).ToString("o")
    $State | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $StatePath -Encoding UTF8
    Write-RunLog -Stage "state" -Message "state saved to $StatePath"
}

# ai-ops-geon Go 모듈을 Linux amd64 바이너리로 빌드하고 실행에 필요한 config/docs를 tar.gz에 묶는다.
function Build-AiOpsPackage {
    $aiOpsFullPath = Resolve-PathFromRoot $AiOpsRoot
    $serviceRoot = Join-Path $aiOpsFullPath "go\service-control-api"
    if (-not (Test-Path -LiteralPath (Join-Path $serviceRoot "go.mod"))) {
        throw "ai-ops-geon service-control-api Go module was not found: $serviceRoot"
    }

    Write-RunLog -Stage "package" -Message "building Linux amd64 binary from $serviceRoot"

    $runFullPath = [System.IO.Path]::GetFullPath($RunDir)
    $packageFullPath = [System.IO.Path]::GetFullPath($PackageRoot)
    if (-not $packageFullPath.StartsWith($runFullPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to recreate package directory outside run directory: $packageFullPath"
    }

    # 재패키징은 RunDir 아래 package 디렉터리만 지운다.
    if (Test-Path -LiteralPath $PackageRoot) {
        Remove-Item -LiteralPath $PackageRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Path $StageDir -Force | Out-Null

    $previousLocation = Get-Location
    $oldGoos = $env:GOOS
    $oldGoarch = $env:GOARCH
    $oldCgo = $env:CGO_ENABLED

    # 원격 VM에서 바로 실행할 수 있도록 Windows에서 Linux용 정적 바이너리를 만든다.
    try {
        Set-Location $serviceRoot
        $env:GOOS = "linux"
        $env:GOARCH = "amd64"
        $env:CGO_ENABLED = "0"

        $binaryPath = Join-Path $StageDir $BinaryName
        & go build -o $binaryPath ./cmd/service-control-api
        if ($LASTEXITCODE -ne 0) {
            throw "go build failed for ai-ops-geon service-control-api"
        }
    } finally {
        Set-Location $previousLocation
        $env:GOOS = $oldGoos
        $env:GOARCH = $oldGoarch
        $env:CGO_ENABLED = $oldCgo
    }

    # 서비스 실행 시 AIOPS_REPO_ROOT 아래에서 찾는 config/docs 파일을 패키지에 포함한다.
    $copyItems = @(
        @{ Source = Join-Path $aiOpsFullPath "config\agent_registry.json"; Destination = Join-Path $StageDir "config\agent_registry.json" },
        @{ Source = Join-Path $aiOpsFullPath "config\ops_llm_benchmark.json"; Destination = Join-Path $StageDir "config\ops_llm_benchmark.json" },
        @{ Source = Join-Path $aiOpsFullPath "config\inference_optimization.json"; Destination = Join-Path $StageDir "config\inference_optimization.json" },
        @{ Source = Join-Path $aiOpsFullPath "docs\submission\openapi_service_control.yaml"; Destination = Join-Path $StageDir "docs\submission\openapi_service_control.yaml" }
    )

    foreach ($item in $copyItems) {
        if (-not (Test-Path -LiteralPath $item.Source)) {
            throw "Required ai-ops-geon package file is missing: $($item.Source)"
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $item.Destination) -Force | Out-Null
        Copy-Item -LiteralPath $item.Source -Destination $item.Destination -Force
    }

    if (Test-Path -LiteralPath $ArchivePath) {
        Remove-Item -LiteralPath $ArchivePath -Force
    }

    Write-RunLog -Stage "package" -Message "creating package archive $ArchivePath"
    tar -czf $ArchivePath -C $StageDir .
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create package archive: $ArchivePath"
    }

    return [pscustomobject]@{
        AiOpsRoot   = $aiOpsFullPath
        ServiceRoot = $serviceRoot
        ArchivePath = [System.IO.Path]::GetFullPath($ArchivePath)
        ArtifactUri = Convert-ToFileUri $ArchivePath
    }
}

# AppDeploy 서버가 떠 있으면 재사용하고, 없으면 SSH runner 환경변수를 주입해 새로 시작한다.
function Start-AppDeployServer {
    param([pscustomobject]$SshConfig)

    New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

    $existing = Get-PortOwner -Port $AppDeployPort
    if ($existing) {
        Write-RunLog -Stage "appdeploy" -Message "reusing existing AppDeploy server on port $AppDeployPort (pid=$($existing.OwningPid))"
        Wait-HttpOk -Uri "http://localhost:$AppDeployPort/api/v1/readiness" -TimeoutSeconds 5 | Out-Null
        return [pscustomobject]@{
            Pid             = $existing.OwningPid
            StartedByScript = $false
            StorePath       = $null
        }
    }

    $credentialRef = [string](Get-Setting $TargetProfileSettings "credential_ref" "cred://nhn-cloud/cpu-vm-001")
    $credentialPrefix = Convert-CredentialRefToEnvPrefix $credentialRef
    $cpuRunner = [string](Get-Setting $AppDeploySettings "cpu_vm_runner" "ssh")
    $gpuRunner = [string](Get-Setting $AppDeploySettings "gpu_vm_runner" "dry-run")
    $sshDefaultTimeout = [string](Get-Setting $AppDeploySettings "ssh_default_timeout" "120s")

    $oldValues = @{
        AIAPP_SERVER_PORT = $env:AIAPP_SERVER_PORT
        AIAPP_STORE_PATH = $env:AIAPP_STORE_PATH
        AIAPP_CPUVM_RUNNER = $env:AIAPP_CPUVM_RUNNER
        AIAPP_GPUVM_RUNNER = $env:AIAPP_GPUVM_RUNNER
        AIAPP_SSH_DEFAULT_TIMEOUT = $env:AIAPP_SSH_DEFAULT_TIMEOUT
        User = [Environment]::GetEnvironmentVariable("${credentialPrefix}_SSH_USER", "Process")
        Key = [Environment]::GetEnvironmentVariable("${credentialPrefix}_SSH_KEY_PATH", "Process")
        Timeout = [Environment]::GetEnvironmentVariable("${credentialPrefix}_SSH_TIMEOUT", "Process")
    }

    # 환경변수는 자식 go run 프로세스에만 전달하고 현재 PowerShell 세션 값은 복구한다.
    try {
        $env:AIAPP_SERVER_PORT = [string]$AppDeployPort
        $env:AIAPP_STORE_PATH = $StorePath
        $env:AIAPP_CPUVM_RUNNER = $cpuRunner
        $env:AIAPP_GPUVM_RUNNER = $gpuRunner
        $env:AIAPP_SSH_DEFAULT_TIMEOUT = $sshDefaultTimeout
        [Environment]::SetEnvironmentVariable("${credentialPrefix}_SSH_USER", $SshConfig.User, "Process")
        [Environment]::SetEnvironmentVariable("${credentialPrefix}_SSH_KEY_PATH", $SshConfig.IdentityFile, "Process")
        [Environment]::SetEnvironmentVariable("${credentialPrefix}_SSH_TIMEOUT", $sshDefaultTimeout, "Process")

        Write-RunLog -Stage "appdeploy" -Message "starting AppDeploy server on port $AppDeployPort"
        $process = Start-Process -FilePath "go" `
            -ArgumentList @("run", "./cmd/server") `
            -WorkingDirectory $Root `
            -RedirectStandardOutput $AppDeployStdout `
            -RedirectStandardError $AppDeployStderr `
            -WindowStyle Hidden `
            -PassThru
    } finally {
        $env:AIAPP_SERVER_PORT = $oldValues.AIAPP_SERVER_PORT
        $env:AIAPP_STORE_PATH = $oldValues.AIAPP_STORE_PATH
        $env:AIAPP_CPUVM_RUNNER = $oldValues.AIAPP_CPUVM_RUNNER
        $env:AIAPP_GPUVM_RUNNER = $oldValues.AIAPP_GPUVM_RUNNER
        $env:AIAPP_SSH_DEFAULT_TIMEOUT = $oldValues.AIAPP_SSH_DEFAULT_TIMEOUT
        [Environment]::SetEnvironmentVariable("${credentialPrefix}_SSH_USER", $oldValues.User, "Process")
        [Environment]::SetEnvironmentVariable("${credentialPrefix}_SSH_KEY_PATH", $oldValues.Key, "Process")
        [Environment]::SetEnvironmentVariable("${credentialPrefix}_SSH_TIMEOUT", $oldValues.Timeout, "Process")
    }

    Wait-HttpOk -Uri "http://localhost:$AppDeployPort/api/v1/readiness" -TimeoutSeconds 45 | Out-Null
    Write-RunLog -Stage "appdeploy" -Message "AppDeploy ready (pid=$($process.Id)); stdout=$AppDeployStdout stderr=$AppDeployStderr"
    return [pscustomobject]@{
        Pid             = $process.Id
        StartedByScript = $true
        StorePath       = $StorePath
    }
}

# AppDeploy /api/v1 호출 공통 wrapper. Windows PowerShell 호환을 위해 JSON body는 UTF-8 byte로 보낸다.
function Invoke-AppDeploy {
    param(
        [string]$Method,
        [string]$Path,
        $Body = $null
    )

    Write-RunLog -Stage "api" -Message "$Method $Path"
    $uri = "http://localhost:$AppDeployPort$Path"
    if ($null -eq $Body) {
        return Invoke-RestMethod -Uri $uri -Method $Method -TimeoutSec 60
    }

    $json = $Body | ConvertTo-Json -Depth 20
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    return Invoke-RestMethod -Uri $uri -Method $Method -ContentType "application/json; charset=utf-8" -Body $bytes -TimeoutSec 120
}

# App Spec, Runtime Profile, Target Profile을 만들고 resource check 후 deployment를 생성한다.
function Register-AndDeploy {
    param(
        [pscustomobject]$PackageInfo,
        [pscustomobject]$SshConfig,
        [string]$RemoteHome
    )

    $runId = (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
    $appName = [string](Get-Setting $AppSettings "name" "aiops-geon-service-control")
    $appVersionPrefix = [string](Get-Setting $AppSettings "version_prefix" "ssh")
    $appDescription = [string](Get-Setting $AppSettings "description" "ai-ops-geon service-control-api deployed by AppDeploy SSH script")
    $runtimeProfileId = "$([string](Get-Setting $RuntimeProfileSettings "id_prefix" "rt-aiops-geon-cpu-ssh-home"))-$runId"
    $targetProfileId = "$([string](Get-Setting $TargetProfileSettings "id_prefix" "target-aiops-geon-cpu-ssh-home"))-$runId"

    $appResources = Get-Setting $AppSettings "resources" $null
    $remoteRunDir = [string](Get-Setting $PackageSettings "remote_run_dir" "run")
    # 원격 artifact 디렉터리에서 패키지를 풀고, PORT/AIOPS_REPO_ROOT를 지정해 service-control API를 실행한다.
    $entryCommand = "rm -rf ./$remoteRunDir && mkdir -p ./$remoteRunDir && tar -xzf $ArchiveName -C ./$remoteRunDir && chmod +x ./$remoteRunDir/$BinaryName && exec env PORT=$RemotePort AIOPS_REPO_ROOT=`"`$PWD/$remoteRunDir`" ./$remoteRunDir/$BinaryName"

    $appSpec = @{
        schema_version = "appspec.khu.ai/v1alpha1"
        kind = "AIApp"
        metadata = @{
            name = $appName
            version = "$appVersionPrefix-$runId"
            description = $appDescription
        }
        artifact = @{
            type = "package"
            uri = $PackageInfo.ArtifactUri
        }
        runtime = @{
            type = [string](Get-Setting $AppSettings "runtime_type" "cpu")
        }
        resources = @{
            cpu = [string](Get-Setting $appResources "cpu" "1")
            memory = [string](Get-Setting $appResources "memory" "512Mi")
        }
        network = @{
            ports = @(
                @{
                    name = "http"
                    app_port = $RemotePort
                    protocol = "TCP"
                }
            )
        }
        entrypoint = @{
            command = "sh"
            args = @("-c", $entryCommand)
        }
        healthcheck = @{
            type = "http"
            path = "/healthz"
        }
    }

    $runtimeProfile = @{
        runtime_profile_id = $runtimeProfileId
        name = [string](Get-Setting $RuntimeProfileSettings "name" "aiops-geon cpu ssh vm process")
        runtime_type = [string](Get-Setting $RuntimeProfileSettings "runtime_type" "cpu")
        adapter_type = [string](Get-Setting $RuntimeProfileSettings "adapter_type" "cpu_vm")
        operating_mode = [string](Get-Setting $RuntimeProfileSettings "operating_mode" "vm_process")
        accelerator = [string](Get-Setting $RuntimeProfileSettings "accelerator" "none")
    }

    $remoteBaseDir = [string](Get-Setting $TargetProfileSettings "remote_base_dir" "aiapp")
    $artifactDir = [string](Get-Setting $TargetProfileSettings "artifact_dir" "$RemoteHome/$remoteBaseDir/artifacts")
    $modelDir = [string](Get-Setting $TargetProfileSettings "model_dir" "$RemoteHome/$remoteBaseDir/models")
    $logDir = [string](Get-Setting $TargetProfileSettings "log_dir" "$RemoteHome/$remoteBaseDir/logs")
    $credentialRef = [string](Get-Setting $TargetProfileSettings "credential_ref" "cred://nhn-cloud/cpu-vm-001")

    $targetProfile = @{
        target_profile_id = $targetProfileId
        name = [string](Get-Setting $TargetProfileSettings "name" "aiops-geon nhn cloud ssh")
        csp = [string](Get-Setting $TargetProfileSettings "csp" "local")
        runtime = @{
            runtime_type = [string](Get-Setting $TargetProfileSettings "runtime_type" "cpu")
            accelerator = [string](Get-Setting $TargetProfileSettings "accelerator" "none")
            operating_mode = [string](Get-Setting $TargetProfileSettings "operating_mode" "vm_process")
        }
        vm = @{
            host = $SshConfig.HostName
            ssh_port = $SshConfig.Port
            credential_ref = $credentialRef
        }
        storage = @{
            artifact_dir = $artifactDir
            model_dir = $modelDir
            log_dir = $logDir
        }
        network = @{
            service_port_range = "$RemotePort-$RemotePort"
        }
    }

    Write-RunLog -Stage "register" -Message "registering app/runtime/target profiles"
    $createdApp = Invoke-AppDeploy -Method Post -Path "/api/v1/apps" -Body @{ app_spec = $appSpec }
    Invoke-AppDeploy -Method Post -Path "/api/v1/runtime-profiles" -Body $runtimeProfile | Out-Null
    Invoke-AppDeploy -Method Post -Path "/api/v1/target-profiles" -Body $targetProfile | Out-Null

    $resourceCheck = Invoke-AppDeploy -Method Post -Path "/api/v1/resources/check" -Body @{
        runtime_profile_id = $runtimeProfileId
        target_profile_id = $targetProfileId
    }
    Write-RunLog -Stage "resource" -Message "resource check status: $($resourceCheck.status)"

    $deployment = Invoke-AppDeploy -Method Post -Path "/api/v1/deployments" -Body @{
        app_version_id = $createdApp.app_version_id
        runtime_profile_id = $runtimeProfileId
        target_profile_id = $targetProfileId
    }
    Write-RunLog -Stage "deploy" -Message "deployment created: $($deployment.deployment_id), status=$($deployment.status)"

    return [pscustomobject]@{
        AppName          = $appName
        AppVersionId     = $createdApp.app_version_id
        RuntimeProfileId = $runtimeProfileId
        TargetProfileId  = $targetProfileId
        ResourceCheck    = $resourceCheck
        Deployment       = $deployment
    }
}

# 외부 공개 포트가 아니라 SSH로 원격 localhost API를 직접 확인한다.
function Invoke-RemoteJsonGet {
    param(
        [string]$Alias,
        [string]$Path
    )

    Write-RunLog -Stage "remote" -Message "GET $Path through SSH alias '$Alias'"
    $remoteCommand = "curl -fsS http://127.0.0.1:$RemotePort$Path"
    $text = (& ssh @SshBatchArgs $Alias $remoteCommand) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw "Remote GET failed: $Path"
    }

    return $text | ConvertFrom-Json
}

# 배포 후 service-operations readiness pipeline이 실제로 응답하는지 확인한다.
function Invoke-RemoteServiceOperation {
    param([string]$Alias)

    $body = @{
        llm_config = [string](Get-Setting $ServiceOperationSettings "llm_config" "config/ops_llm_benchmark.json")
        inference_config = [string](Get-Setting $ServiceOperationSettings "inference_config" "config/inference_optimization.json")
        llm_policy = [string](Get-Setting $ServiceOperationSettings "llm_policy" "quality_first")
        workload = [string](Get-Setting $ServiceOperationSettings "workload" "llm-chat-inference")
        recovery_namespace = [string](Get-Setting $ServiceOperationSettings "recovery_namespace" "aiops-demo")
        recovery_deployment = [string](Get-Setting $ServiceOperationSettings "recovery_deployment" "aiops-service")
        mode = [string](Get-Setting $ServiceOperationSettings "mode" "mock")
        guard_backend = [string](Get-Setting $ServiceOperationSettings "guard_backend" "go")
    } | ConvertTo-Json -Compress -Depth 20

    Write-RunLog -Stage "remote" -Message "POST /api/v1/service-operations/run through SSH alias '$Alias'"
    $payload = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($body))
    $remoteCommand = "printf '%s' '$payload' | base64 -d | curl -fsS -X POST -H 'content-type: application/json' -d @- http://127.0.0.1:$RemotePort/api/v1/service-operations/run"
    $text = (& ssh @SshBatchArgs $Alias $remoteCommand) -join "`n"
    if ($LASTEXITCODE -ne 0) {
        throw "Remote service operation failed"
    }

    return $text | ConvertFrom-Json
}

# 로컬 tunnel_port를 원격 remote_port로 포워딩한다. 이미 있으면 health 확인 후 재사용한다.
function Start-Tunnel {
    param([string]$Alias)

    $existing = Get-PortOwner -Port $TunnelPort
    if ($existing) {
        Write-RunLog -Stage "tunnel" -Message "reusing existing SSH tunnel on local port $TunnelPort (pid=$($existing.OwningPid))"
        Wait-HttpOk -Uri "http://localhost:$TunnelPort/healthz" -TimeoutSeconds 10 | Out-Null
        return [pscustomobject]@{
            Pid             = $existing.OwningPid
            StartedByScript = $false
        }
    }

    Write-RunLog -Stage "tunnel" -Message "starting local tunnel http://localhost:$TunnelPort -> remote 127.0.0.1:$RemotePort"
    $process = Start-Process -FilePath "ssh" `
        -ArgumentList @("-o", "LogLevel=ERROR", "-o", "ExitOnForwardFailure=yes", "-N", "-L", "$TunnelPort`:127.0.0.1:$RemotePort", $Alias) `
        -RedirectStandardOutput $TunnelStdout `
        -RedirectStandardError $TunnelStderr `
        -WindowStyle Hidden `
        -PassThru

    Wait-HttpOk -Uri "http://localhost:$TunnelPort/healthz" -TimeoutSeconds 15 | Out-Null
    Write-RunLog -Stage "tunnel" -Message "SSH tunnel ready (pid=$($process.Id)); stdout=$TunnelStdout stderr=$TunnelStderr"
    return [pscustomobject]@{
        Pid             = $process.Id
        StartedByScript = $true
    }
}

# script-owned 프로세스만 끄는 것이 기본이나, tunnel처럼 명확한 ssh 프로세스는 강제 종료할 수 있다.
function Stop-ProcessIfOwned {
    param(
        [Nullable[int]]$ProcessId,
        [bool]$StartedByScript,
        [string]$Label,
        [bool]$ForceWhenNotOwned = $false,
        [string]$ExpectedProcessName = ""
    )

    if (-not $ProcessId) {
        Write-RunLog -Stage "stop" -Message "$Label pid is empty"
        return $null
    }

    if (-not $StartedByScript -and -not $ForceWhenNotOwned) {
        Write-RunLog -Stage "stop" -Message "$Label was not started by this script; leaving it running"
        return $null
    }

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($process) {
        if (-not [string]::IsNullOrWhiteSpace($ExpectedProcessName) -and $process.ProcessName -ne $ExpectedProcessName) {
            Write-RunLog -Stage "stop" -Level "WARN" -Message "$Label pid=$ProcessId is $($process.ProcessName), not $ExpectedProcessName; leaving it running"
            return $null
        }

        Write-RunLog -Stage "stop" -Message "stopping $Label pid=$ProcessId"
        Stop-Process -Id $ProcessId -Force
        return $ProcessId
    } else {
        Write-RunLog -Stage "stop" -Message "$Label pid=$ProcessId is not running"
        return $null
    }
}

# stop action에서 로컬 SSH tunnel을 닫는다. 재사용 tunnel도 owner가 ssh면 종료한다.
function Stop-LocalTunnel {
    param($State)

    if (-not $StopTunnelOnStop) {
        Write-RunLog -Stage "stop" -Message "stop_tunnel=false; leaving local tunnel untouched"
        return $null
    }

    if ($State -and $State.tunnel_pid) {
        $stoppedPid = Stop-ProcessIfOwned -ProcessId $State.tunnel_pid -StartedByScript ([bool]$State.tunnel_started_by_script) -Label "SSH tunnel" -ForceWhenNotOwned $true -ExpectedProcessName "ssh"
        if ($stoppedPid) {
            return $stoppedPid
        }
    }

    $owner = Get-PortOwner -Port $TunnelPort
    if (-not $owner) {
        Write-RunLog -Stage "stop" -Message "no local tunnel listener on port $TunnelPort"
        return $null
    }

    return Stop-ProcessIfOwned -ProcessId $owner.OwningPid -StartedByScript $false -Label "SSH tunnel on port $TunnelPort" -ForceWhenNotOwned $true -ExpectedProcessName "ssh"
}

# AppDeploy stop API를 호출하고, 바로 deployment log를 터미널에 보여준다.
function Stop-DeploymentById {
    param([string]$DeploymentId)

    if ([string]::IsNullOrWhiteSpace($DeploymentId)) {
        return $null
    }

    Invoke-AppDeploy -Method Post -Path "/api/v1/deployments/$DeploymentId/stop" | Out-Null
    Write-RunLog -Stage "stop" -Message "stop requested for deployment $DeploymentId"
    Write-DeploymentLogs -DeploymentId $DeploymentId
    return $DeploymentId
}

# 여러 번 deploy한 경우 state에 없는 이전 RUNNING deployment까지 찾아 중지한다.
function Get-MatchingRunningDeployments {
    $runtimePrefix = [string](Get-Setting $RuntimeProfileSettings "id_prefix" "rt-aiops-geon-cpu-ssh-home")
    $targetPrefix = [string](Get-Setting $TargetProfileSettings "id_prefix" "target-aiops-geon-cpu-ssh-home")
    $deploymentList = Invoke-AppDeploy -Method Get -Path "/api/v1/deployments"

    return @($deploymentList.deployments) | Where-Object {
        $_.status -eq "RUNNING" -and (
            ([string]$_.runtime_profile_id).StartsWith($runtimePrefix) -or
            ([string]$_.target_profile_id).StartsWith($targetPrefix)
        )
    }
}

# 최근 deployment event를 터미널에 요약 출력한다.
function Write-DeploymentLogs {
    param([string]$DeploymentId)

    if ([string]::IsNullOrWhiteSpace($DeploymentId)) {
        return
    }

    try {
        $logs = Invoke-AppDeploy -Method Get -Path "/api/v1/deployments/$DeploymentId/logs"
        $items = @($logs.items)
        if ($items.Count -eq 0) {
            Write-RunLog -Stage "deployment-log" -Message "no deployment logs returned"
            return
        }

        foreach ($item in ($items | Select-Object -Last 8)) {
            Write-RunLog -Stage "deployment-log" -Message "[$($item.stage)] $($item.component): $($item.message)"
        }
    } catch {
        Write-RunLog -Stage "deployment-log" -Level "WARN" -Message "could not read deployment logs: $($_.Exception.Message)"
    }
}

# AppDeploy 상태, state deployment, 원격 API, 로컬 터널 상태를 한 번에 확인한다.
function Show-Status {
    Write-RunLog -Stage "status" -Message "checking AppDeploy, remote health, and local tunnel"
    $state = Read-State
    $appDeployReady = $false
    $deployment = $null
    $remoteHealth = $null
    $tunnelHealth = $null

    try {
        Wait-HttpOk -Uri "http://localhost:$AppDeployPort/api/v1/readiness" -TimeoutSeconds 3 | Out-Null
        $appDeployReady = $true
        Write-RunLog -Stage "status" -Message "AppDeploy readiness: ready"
    } catch {
        $appDeployReady = $false
        Write-RunLog -Stage "status" -Level "WARN" -Message "AppDeploy readiness failed: $($_.Exception.Message)"
    }

    if ($state -and $appDeployReady -and $state.deployment_id) {
        try {
            $deployment = Invoke-AppDeploy -Method Get -Path "/api/v1/deployments/$($state.deployment_id)"
            Write-RunLog -Stage "status" -Message "deployment $($state.deployment_id): $($deployment.status)"
            Write-DeploymentLogs -DeploymentId $state.deployment_id
        } catch {
            $deployment = @{ error = $_.Exception.Message }
            Write-RunLog -Stage "status" -Level "WARN" -Message "deployment status failed: $($_.Exception.Message)"
        }
    } elseif (-not $state) {
        Write-RunLog -Stage "status" -Level "WARN" -Message "state file does not exist: $StatePath"
    }

    try {
        $remoteHealth = Invoke-RemoteJsonGet -Alias $SshAlias -Path "/healthz"
        Write-RunLog -Stage "status" -Message "remote health: $($remoteHealth.status)"
    } catch {
        $remoteHealth = @{ error = $_.Exception.Message }
        Write-RunLog -Stage "status" -Level "WARN" -Message "remote health failed: $($_.Exception.Message)"
    }

    try {
        $tunnelHealth = Invoke-RestMethod -Uri "http://localhost:$TunnelPort/healthz" -Method Get -TimeoutSec 3
        Write-RunLog -Stage "status" -Message "tunnel health: $($tunnelHealth.status)"
    } catch {
        $tunnelHealth = @{ error = $_.Exception.Message }
        Write-RunLog -Stage "status" -Level "WARN" -Message "tunnel health failed: $($_.Exception.Message)"
    }

    return [pscustomobject]@{
        config_path      = $ConfigFullPath
        state_path       = $StatePath
        state_exists     = [bool]$state
        appdeploy_ready  = $appDeployReady
        deployment       = $deployment
        remote_health    = $remoteHealth
        tunnel_health    = $tunnelHealth
        local_tunnel_url = "http://localhost:$TunnelPort"
    }
}

# 마지막 state deployment와 같은 prefix의 RUNNING deployment를 모두 중지하고 터널을 닫는다.
function Stop-Run {
    Write-RunLog -Stage "stop" -Message "stopping matching deployments and local tunnel"
    $state = Read-State
    $stopErrors = @()
    $stoppedDeployments = @()
    $seenDeployments = @{}

    if (-not $state) {
        Write-RunLog -Stage "stop" -Level "WARN" -Message "no state file found at $StatePath; will still stop matching running deployments"
    }

    if ($state -and $state.deployment_id) {
        try {
            $stopped = Stop-DeploymentById -DeploymentId $state.deployment_id
            if ($stopped) {
                $stoppedDeployments += $stopped
                $seenDeployments[$stopped] = $true
            }
        } catch {
            $stopErrors += "deployment stop: $($_.Exception.Message)"
            Write-RunLog -Stage "stop" -Level "WARN" -Message "deployment stop failed: $($_.Exception.Message)"
        }
    }

    if ($StopAllMatchingDeployments) {
        try {
            $runningDeployments = @(Get-MatchingRunningDeployments)
            foreach ($deployment in $runningDeployments) {
                if ($seenDeployments.ContainsKey($deployment.deployment_id)) {
                    continue
                }

                try {
                    $stopped = Stop-DeploymentById -DeploymentId $deployment.deployment_id
                    if ($stopped) {
                        $stoppedDeployments += $stopped
                        $seenDeployments[$stopped] = $true
                    }
                } catch {
                    $stopErrors += "deployment stop $($deployment.deployment_id): $($_.Exception.Message)"
                    Write-RunLog -Stage "stop" -Level "WARN" -Message "deployment stop failed for $($deployment.deployment_id): $($_.Exception.Message)"
                }
            }
        } catch {
            $stopErrors += "list running deployments: $($_.Exception.Message)"
            Write-RunLog -Stage "stop" -Level "WARN" -Message "could not list matching running deployments: $($_.Exception.Message)"
        }
    }

    $stoppedTunnelPid = Stop-LocalTunnel -State $state

    $stoppedAppDeployPid = $null
    if ($state -and $state.appdeploy_pid) {
        if ($StopAppDeployMode -eq "always") {
            $stoppedAppDeployPid = Stop-ProcessIfOwned -ProcessId $state.appdeploy_pid -StartedByScript ([bool]$state.appdeploy_started_by_script) -Label "AppDeploy server" -ForceWhenNotOwned $true
        } elseif ($StopAppDeployMode -eq "if_started_by_script") {
            $stoppedAppDeployPid = Stop-ProcessIfOwned -ProcessId $state.appdeploy_pid -StartedByScript ([bool]$state.appdeploy_started_by_script) -Label "AppDeploy server"
        } else {
            Write-RunLog -Stage "stop" -Message "stop_appdeploy=$StopAppDeployMode; leaving AppDeploy server running"
        }
    }

    if ($state) {
        $stateTable = ConvertTo-Hashtable $state
        $stateTable["deployment_status"] = "STOPPED"
        if ($stoppedTunnelPid) {
            $stateTable["tunnel_pid"] = $null
            $stateTable["tunnel_started_by_script"] = $false
            $stateTable["local_tunnel_url"] = $null
        }
        Write-State -State $stateTable
    }

    return [pscustomObject]@{
        state_path = $StatePath
        deployment_id = if ($state) { $state.deployment_id } else { $null }
        stopped_deployments = $stoppedDeployments
        stopped_tunnel_pid = $stoppedTunnelPid
        stopped_appdeploy_pid = $stoppedAppDeployPid
        errors = $stopErrors
    }
}

Write-RunLog -Stage "config" -Message "loaded config: $ConfigFullPath"
Write-RunLog -Stage "config" -Message "ssh_alias=$SshAlias appdeploy_port=$AppDeployPort remote_port=$RemotePort tunnel_port=$TunnelPort run_root=$RunRoot"

# 사용자가 지정한 Action 하나만 실행한다.
try {
    switch ($Action) {
        "package" {
            New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
            $package = Build-AiOpsPackage
            Write-JsonResult $package
            break
        }

        "status" {
            $status = Show-Status
            Write-JsonResult $status
            break
        }

        "stop" {
            $stopResult = Stop-Run
            Write-JsonResult $stopResult
            break
        }

        "tunnel" {
            New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
            $tunnel = Start-Tunnel -Alias $SshAlias
            $state = Read-State
            if (-not $state) {
                $state = @{}
            } else {
                $state = ConvertTo-Hashtable $state
            }
            $state["ssh_alias"] = $SshAlias
            $state["remote_port"] = $RemotePort
            $state["tunnel_port"] = $TunnelPort
            $state["tunnel_pid"] = $tunnel.Pid
            $state["tunnel_started_by_script"] = $tunnel.StartedByScript
            $state["local_tunnel_url"] = "http://localhost:$TunnelPort"
            Write-State -State $state
            $status = Show-Status
            Write-JsonResult $status
            break
        }

        "deploy" {
            New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
            Write-RunLog -Stage "deploy" -Message "starting ai-ops-geon AppDeploy SSH deployment flow"
            # deploy 흐름: SSH 확인 -> 패키징 -> AppDeploy 준비 -> 등록/배포 -> 원격 검증 -> 터널 생성 -> state 저장.
            $sshConfig = Get-SshHostConfig -Alias $SshAlias
            $remoteHome = Get-RemoteHome -Alias $SshAlias
            $package = Build-AiOpsPackage
            $server = Start-AppDeployServer -SshConfig $sshConfig
            $deployResult = Register-AndDeploy -PackageInfo $package -SshConfig $sshConfig -RemoteHome $remoteHome

            Start-Sleep -Seconds 2
            $remoteHealth = Invoke-RemoteJsonGet -Alias $SshAlias -Path "/healthz"
            $agents = Invoke-RemoteJsonGet -Alias $SshAlias -Path "/api/v1/agents"
            $operation = Invoke-RemoteServiceOperation -Alias $SshAlias
            Write-RunLog -Stage "verify" -Message "remote health=$($remoteHealth.status), agents=$(@($agents.agents).Count), operation_valid=$($operation.valid)"

            # 터널은 선택 사항이지만 기본값은 로컬에서 http://localhost:tunnel_port로 접근 가능하게 만든다.
            $tunnel = $null
            if (-not $SkipTunnel) {
                $tunnel = Start-Tunnel -Alias $SshAlias
            } else {
                Write-RunLog -Stage "tunnel" -Message "skip_tunnel=true; local tunnel was not started"
            }

            # 이후 status/stop/tunnel action이 같은 실행분을 찾을 수 있게 최소 상태를 저장한다.
            $state = @{
                appdeploy_port = $AppDeployPort
                appdeploy_pid = $server.Pid
                appdeploy_started_by_script = $server.StartedByScript
                appdeploy_url = "http://localhost:$AppDeployPort"
                ssh_alias = $SshAlias
                remote_port = $RemotePort
                tunnel_port = if ($SkipTunnel) { $null } else { $TunnelPort }
                tunnel_pid = if ($tunnel) { $tunnel.Pid } else { $null }
                tunnel_started_by_script = if ($tunnel) { $tunnel.StartedByScript } else { $false }
                run_dir = $RunDir
                store_path = $server.StorePath
                archive_path = $package.ArchivePath
                app_name = $deployResult.AppName
                app_version_id = $deployResult.AppVersionId
                runtime_profile_id = $deployResult.RuntimeProfileId
                target_profile_id = $deployResult.TargetProfileId
                deployment_id = $deployResult.Deployment.deployment_id
                deployment_status = $deployResult.Deployment.status
                local_tunnel_url = if ($SkipTunnel) { $null } else { "http://localhost:$TunnelPort" }
                config_path = $ConfigFullPath
            }
            Write-State -State $state
            Write-DeploymentLogs -DeploymentId $deployResult.Deployment.deployment_id

            $result = [pscustomobject]@{
                deployment_id    = $deployResult.Deployment.deployment_id
                deployment_status = $deployResult.Deployment.status
                resource_check    = $deployResult.ResourceCheck.status
                remote_health     = $remoteHealth
                agent_count       = @($agents.agents).Count
                operation_valid   = $operation.valid
                selected_resource = $operation.selected_resource
                local_tunnel_url  = if ($SkipTunnel) { $null } else { "http://localhost:$TunnelPort" }
                state_path        = $StatePath
                config_path       = $ConfigFullPath
            }
            Write-JsonResult $result
            break
        }
    }
} catch {
    # 실패 시 터미널 로그만 보고도 원인을 찾을 수 있게 관련 stderr tail을 덧붙인다.
    Write-RunLog -Stage "error" -Level "ERROR" -Message $_.Exception.Message
    Write-RecentFileLines -Path $AppDeployStderr -Label "AppDeploy stderr"
    Write-RecentFileLines -Path $TunnelStderr -Label "Tunnel stderr"
    throw
}
