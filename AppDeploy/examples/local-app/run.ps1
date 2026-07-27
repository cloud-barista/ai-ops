Write-Output "local demo process started"
while ($true) {
    Write-Output ("local demo heartbeat " + (Get-Date -Format o))
    Start-Sleep -Seconds 1
}

