function Test-KhuCloudGroupCleaned {
    param($Group,[string]$JournalDirectory)
    if($null -eq $Group -or $Group.target_profile_id -notmatch '^khu-vm-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$') { return $false }
    $RunId='run-'+$Matches[1]
    $Path=Join-Path $JournalDirectory ($RunId+'.json')
    if(-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    try { $Run=Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json } catch { return $false }
    return ($Run.run_id -eq $RunId -and
        $Run.intent.flow_id -eq $Group.flow_id -and
        $Run.app.app_version_id -eq $Group.app_version_id -and
        $Run.app.target_profile_id -eq $Group.target_profile_id -and
        $Run.state -eq 'CLEANED' -and
        $Run.cleanup.confirmed -eq $true -and
        $Run.cleanup.attachments.verified -eq $true -and
        @($Run.cleanup.attachments.remaining).Count -eq 0 -and
        $Run.last_provider_status.state -eq 'Terminated' -and
        $Run.last_provider_status.instance_id -eq $Run.resource.provider_instance_id)
}
