param([ValidateSet('Review','Execute')][string]$Mode='Review',[switch]$ApproveCost,[double]$ComputeBudgetUsd=0)
$ErrorActionPreference='Stop'
$TaskCampaignLauncher=Join-Path $PSScriptRoot 'Run-KhuCloudValidation.ps1'
$TaskCampaignArgs=@('-NoProfile','-ExecutionPolicy','Bypass','-File',$TaskCampaignLauncher,'-Mode',$Mode,'-Kind','cpu','-VmCountCampaign','-SshRoute','ssm','-LifetimeSeconds','2400','-ComputeBudgetUsd',$ComputeBudgetUsd.ToString('R',[Globalization.CultureInfo]::InvariantCulture))
if($ApproveCost){$TaskCampaignArgs+='-ApproveCost'}
& powershell @TaskCampaignArgs
exit $LASTEXITCODE
