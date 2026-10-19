$ErrorActionPreference='Stop'
$global:KhuCampaignTestCapturedArgs=@()
function global:powershell { $global:KhuCampaignTestCapturedArgs=@($args); $global:LASTEXITCODE=0 }
try {
    & (Join-Path $PSScriptRoot '../scripts/Run-KhuCloudCampaign.ps1') -Mode Execute -ApproveCost -ComputeBudgetUsd 0.4608
    if($global:KhuCampaignTestCapturedArgs -notcontains '-ApproveCost'){throw 'Approved switch must reach Windows PowerShell as a standalone flag, not a boolean string'}
    if($global:KhuCampaignTestCapturedArgs -notcontains '-VmCountCampaign'){throw 'Campaign dispatch flag missing'}
    Write-Output 'PASS: approved campaign flags reach the existing launcher without string-to-boolean conversion'
}finally{Remove-Item -LiteralPath Function:/powershell;Remove-Variable -Name KhuCampaignTestCapturedArgs -Scope Global}
