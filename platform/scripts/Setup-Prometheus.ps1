param()
$ErrorActionPreference='Stop'
$Workspace=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$Directory=Join-Path $Workspace 'output\khu-platform-validation\runtime\prometheus'
$Version='3.13.4'
$Checksum='9f8950c9c5092366368b674b098250c82e998f62b90e2fbe16e1a3bf773a1a0e'
$Archive=Join-Path $Directory ('prometheus-'+$Version+'.windows-amd64.zip')
New-Item -ItemType Directory -Path $Directory -Force | Out-Null
if(-not (Test-Path -LiteralPath $Archive)) {
    Invoke-WebRequest -Uri ('https://github.com/prometheus/prometheus/releases/download/v'+$Version+'/prometheus-'+$Version+'.windows-amd64.zip') -OutFile $Archive
}
if((Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Checksum){throw 'Prometheus archive checksum does not match the official release. No binary was executed.'}
$BinaryDirectory=Join-Path $Directory ('prometheus-'+$Version+'.windows-amd64')
if(-not (Test-Path -LiteralPath (Join-Path $BinaryDirectory 'prometheus.exe'))){Expand-Archive -LiteralPath $Archive -DestinationPath $Directory}
& (Join-Path $BinaryDirectory 'promtool.exe') check config (Join-Path $Workspace 'platform\monitoring\prometheus.yml')
if($LASTEXITCODE -ne 0){throw 'Prometheus configuration is invalid'}
@{version=$Version;archive_sha256=$Checksum;source='https://prometheus.io/download/';binary=(Join-Path $BinaryDirectory 'prometheus.exe');config=(Join-Path $Workspace 'platform\monitoring\prometheus.yml');port=18190} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Directory 'ready.json') -Encoding UTF8
Write-Output 'Prometheus is prepared locally. No service, cloud VM or lab server was started.'
