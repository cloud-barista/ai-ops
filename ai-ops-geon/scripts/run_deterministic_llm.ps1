param([Parameter(Mandatory = $true)][int]$Port)

$ErrorActionPreference = "Stop"
$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Parse("127.0.0.1"), $Port)
$listener.Start()
try {
    while ($true) {
        $client = $listener.AcceptTcpClient()
        $stream = $client.GetStream()
        try {
            $headerBytes = [Collections.Generic.List[byte]]::new()
            while ($true) {
                $byte = $stream.ReadByte()
                if ($byte -lt 0) { throw "LLM request ended before headers were complete" }
                $headerBytes.Add([byte]$byte)
                if ($headerBytes.Count -ge 4 -and
                    $headerBytes[$headerBytes.Count - 4] -eq 13 -and
                    $headerBytes[$headerBytes.Count - 3] -eq 10 -and
                    $headerBytes[$headerBytes.Count - 2] -eq 13 -and
                    $headerBytes[$headerBytes.Count - 1] -eq 10) { break }
            }
            $headers = [Text.Encoding]::ASCII.GetString($headerBytes.ToArray())
            $lengthMatch = [Text.RegularExpressions.Regex]::Match($headers, "(?im)^Content-Length:\s*(\d+)")
            $contentLength = if ($lengthMatch.Success) { [int]$lengthMatch.Groups[1].Value } else { 0 }
            $requestBytes = New-Object byte[] $contentLength
            $offset = 0
            while ($offset -lt $contentLength) {
                $read = $stream.Read($requestBytes, $offset, $contentLength - $offset)
                if ($read -le 0) { throw "LLM request body ended early" }
                $offset += $read
            }
            $body = [Text.Encoding]::UTF8.GetString($requestBytes) | ConvertFrom-Json
            $userMessage = @($body.messages | Where-Object { $_.role -eq "user" })[0].content
            $planningInput = ($userMessage -replace '^Deployment planning input:\s*', '') | ConvertFrom-Json
            $manifest = @{
                schema_version = "deployment.khu.ai/v1alpha1"
                kind = "DeploymentManifest"
                spec = @{
                    app_version_id = $planningInput.app_version_id
                    accelerator = "none"
                    resources = @{ cpu = "1"; memory = "256Mi"; gpu = "0"; storage = "1Gi" }
                }
            }
            $content = ($manifest | ConvertTo-Json -Compress -Depth 10)
            $response = @{ choices = @(@{ message = @{ content = $content } }) } | ConvertTo-Json -Compress -Depth 10
            $responseBytes = [Text.Encoding]::UTF8.GetBytes($response)
            $responseHeaders = "HTTP/1.1 200 OK`r`nContent-Type: application/json`r`nContent-Length: $($responseBytes.Length)`r`nConnection: close`r`n`r`n"
            $headerResponseBytes = [Text.Encoding]::ASCII.GetBytes($responseHeaders)
            $stream.Write($headerResponseBytes, 0, $headerResponseBytes.Length)
            $stream.Write($responseBytes, 0, $responseBytes.Length)
            $stream.Flush()
        } finally {
            $stream.Close()
            $client.Close()
        }
    }
} finally {
    $listener.Stop()
}
