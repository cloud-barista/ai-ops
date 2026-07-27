# HTTP API를 사용해 package 생성부터 App 등록, 자원 점검, 배포까지 실행한다.
# Windows PowerShell 5.1 호환을 위해 multipart 요청은 System.Net.Http로 구성한다.
param(
    [ValidateSet("build", "deploy")]
    [string]$Action = "deploy",

    [ValidateSet("aiops-geon-service-control", "go", "python", "node", "binary", "script")]
    [string]$PackageType = "script",

    [string]$Source = "",
    [string]$AppName = "",
    [string]$AppVersion = "0.1.0",
    [string]$Entrypoint = "",

    [ValidateSet("cpu", "gpu")]
    [string]$RuntimeType = "cpu",

    [ValidateRange(0, 65535)]
    [int]$ServicePort = 0,

    [string]$HealthcheckPath = "/health",
    [string]$TargetProfileId = "",
    [string]$BaseUrl = "http://localhost:8080"
)

$ErrorActionPreference = "Stop"
$Base = $BaseUrl.TrimEnd("/")
$RequestId = "req-package-shell-$([Guid]::NewGuid().ToString('N'))"

function Invoke-JsonApi {
    param(
        [string]$Method,
        [string]$Path,
        $Body = $null
    )

    $parameters = @{
        Method = $Method
        Uri = "$Base$Path"
        Headers = @{
            "X-Request-ID" = $RequestId
            "Accept" = "application/json"
        }
        TimeoutSec = 300
    }
    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 50
        $parameters["ContentType"] = "application/json; charset=utf-8"
        $parameters["Body"] = [System.Text.Encoding]::UTF8.GetBytes($json)
    }

    try {
        return Invoke-RestMethod @parameters
    } catch {
        $details = $_.ErrorDetails.Message
        if (-not [string]::IsNullOrWhiteSpace($details)) {
            throw "API $Method $Path failed: $details"
        }
        throw
    }
}

function Invoke-PackageUpload {
    param([string]$SourcePath)

    Add-Type -AssemblyName System.Net.Http
    $handler = New-Object System.Net.Http.HttpClientHandler
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromMinutes(5)
    $request = $null
    $form = $null
    $stream = $null
    $fileContent = $null
    $requestOwnsForm = $false
    $formOwnsFileContent = $false
    $fileContentOwnsStream = $false
    try {
        $request = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Post, "$Base/api/v1/artifacts/packages")
        $request.Headers.Add("X-Request-ID", $RequestId)
        $request.Headers.Add("Accept", "application/json")
        $form = New-Object System.Net.Http.MultipartFormDataContent

        $fields = [ordered]@{
            package_type = $PackageType
            app_name = $AppName
            app_version = $AppVersion
            entrypoint = $Entrypoint
            runtime_type = $RuntimeType
        }
        if ($ServicePort -gt 0) {
            $fields["service_port"] = [string]$ServicePort
            $fields["healthcheck_path"] = $HealthcheckPath
        }
        foreach ($field in $fields.GetEnumerator()) {
            $content = New-Object System.Net.Http.StringContent([string]$field.Value, [System.Text.Encoding]::UTF8)
            $form.Add($content, [string]$field.Key)
        }

        $stream = [System.IO.File]::OpenRead($SourcePath)
        $fileContent = New-Object System.Net.Http.StreamContent($stream)
        $fileContentOwnsStream = $true
        $fileContent.Headers.ContentType = New-Object System.Net.Http.Headers.MediaTypeHeaderValue("application/octet-stream")
        $form.Add($fileContent, "source", [System.IO.Path]::GetFileName($SourcePath))
        $formOwnsFileContent = $true
        $request.Content = $form
        $requestOwnsForm = $true

        $response = $client.SendAsync($request).GetAwaiter().GetResult()
        try {
            $raw = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            if (-not $response.IsSuccessStatusCode) {
                throw "API POST /api/v1/artifacts/packages failed: HTTP $([int]$response.StatusCode) $raw"
            }
            return $raw | ConvertFrom-Json
        } finally {
            $response.Dispose()
        }
    } finally {
        if ($null -ne $request) {
            $request.Dispose()
        }
        if (-not $requestOwnsForm -and $null -ne $form) {
            $form.Dispose()
        }
        if (-not $formOwnsFileContent -and $null -ne $fileContent) {
            $fileContent.Dispose()
        }
        if (-not $fileContentOwnsStream -and $null -ne $stream) {
            $stream.Dispose()
        }
        $client.Dispose()
        $handler.Dispose()
    }
}

function Get-DefaultAppName {
    param([string]$Path)

    $name = [System.IO.Path]::GetFileNameWithoutExtension($Path).ToLowerInvariant()
    $name = ($name -replace "[^a-z0-9]+", "-").Trim("-")
    if ($name.Length -lt 2) {
        $name = "app-$name".Trim("-")
    }
    if ([string]::IsNullOrWhiteSpace($name)) {
        $name = "app-upload"
    }
    if ($name.Length -gt 63) {
        $name = $name.Substring(0, 63).TrimEnd("-")
    }
    return $name
}

function Get-DefaultEntrypoint {
    param(
        [string]$Type,
        [string]$Path
    )

    if ($Type -ne "go" -and [System.IO.Path]::GetExtension($Path) -ne ".zip") {
        return [System.IO.Path]::GetFileName($Path)
    }
    switch ($Type) {
        "go" { return "." }
        "python" { return "main.py" }
        "node" { return "index.js" }
        "script" { return "run.sh" }
        default { return "" }
    }
}

$SourceFullPath = ""
if ($PackageType -ne "aiops-geon-service-control") {
    if ([string]::IsNullOrWhiteSpace($Source)) {
        throw "-Source is required for $PackageType packages"
    }
    $SourceFullPath = (Resolve-Path -LiteralPath $Source).Path
    $sourceItem = Get-Item -LiteralPath $SourceFullPath
    if ($sourceItem.PSIsContainer -or $sourceItem.Length -le 0) {
        throw "-Source must be a non-empty file"
    }
    if ([string]::IsNullOrWhiteSpace($AppName)) {
        $AppName = Get-DefaultAppName -Path $SourceFullPath
    }
    if ([string]::IsNullOrWhiteSpace($Entrypoint)) {
        $Entrypoint = Get-DefaultEntrypoint -Type $PackageType -Path $SourceFullPath
    }
    if ([string]::IsNullOrWhiteSpace($Entrypoint)) {
        throw "-Entrypoint is required for this ZIP package"
    }
}

if ($PackageType -eq "aiops-geon-service-control") {
    if ($ServicePort -eq 0) {
        $ServicePort = 18089
    }
    $packageRequest = @{
        preset = $PackageType
        service_port = $ServicePort
    }
    if (-not [string]::IsNullOrWhiteSpace($AppVersion)) {
        $packageRequest["app_version"] = $AppVersion
    }
    $package = Invoke-JsonApi -Method "POST" -Path "/api/v1/artifacts/packages" -Body $packageRequest
} else {
    $package = Invoke-PackageUpload -SourcePath $SourceFullPath
}

if ($Action -eq "build") {
    $package | ConvertTo-Json -Depth 50
    return
}

$app = $null
$resourceCheck = $null
try {
    $app = Invoke-JsonApi -Method "POST" -Path "/api/v1/apps" -Body @{ app_spec = $package.app_spec }
    if (-not [string]::IsNullOrWhiteSpace($TargetProfileId)) {
        $resourceBody = @{ target_profile_id = $TargetProfileId }
        $resourceCheck = Invoke-JsonApi -Method "POST" -Path "/api/v1/resources/check" -Body $resourceBody
        if ([string]$resourceCheck.status -ne "available") {
            throw "Resource Check status is '$($resourceCheck.status)'; Deployment was not created"
        }
    }
    $deploymentBody = @{
        app_version_id = $app.app_version_id
        requested_by = "appdeploy-powershell-package"
    }
    if (-not [string]::IsNullOrWhiteSpace($TargetProfileId)) {
        $deploymentBody.target_profile_id = $TargetProfileId
    }
    $deployment = Invoke-JsonApi -Method "POST" -Path "/api/v1/deployments" -Body $deploymentBody
} catch {
    $partialResult = [ordered]@{
        archive_name = $package.archive_name
        artifact_uri = $package.artifact_uri
        app_id = if ($null -ne $app) { $app.app_id } else { $null }
        app_version_id = if ($null -ne $app) { $app.app_version_id } else { $null }
        resource_check_status = if ($null -ne $resourceCheck) { $resourceCheck.status } else { $null }
        resource_checks = if ($null -ne $resourceCheck) { $resourceCheck.checks } else { $null }
    }
    [Console]::Error.WriteLine("partial_result: $($partialResult | ConvertTo-Json -Compress -Depth 20)")
    throw
}

[pscustomobject]@{
    package = $package
    app = $app
    resource_check = $resourceCheck
    deployment = $deployment
} | ConvertTo-Json -Depth 50
