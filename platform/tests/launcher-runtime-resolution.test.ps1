$ErrorActionPreference='Stop'
$TaskLauncher=Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts\Run-KhuResearchPoc.ps1'
$TaskTokens=$null;$TaskErrors=$null
$TaskAst=[System.Management.Automation.Language.Parser]::ParseFile($TaskLauncher,[ref]$TaskTokens,[ref]$TaskErrors)
if($TaskErrors.Count){throw 'Launcher has PowerShell syntax errors'}
$TaskResolver=$TaskAst.Find({param($Node) $Node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $Node.Name -eq 'Resolve-PocNode'},$true)
if(-not $TaskResolver){throw 'Node resolution must work without a PATH-installed Node'}
# Load only dependency resolution; never run the launcher or mutate services.
. ([scriptblock]::Create($TaskResolver.Extent.Text))
$TaskNode=Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
if(-not(Test-Path -LiteralPath $TaskNode -PathType Leaf)){throw 'The existing bundled Node runtime is required for this regression check'}
$TaskSystemPath=Join-Path $env:SystemRoot 'System32'
$TaskSavedPath=$env:PATH
try {
    $env:PATH=$TaskSystemPath
    if(Get-Command node -CommandType Application -ErrorAction SilentlyContinue){throw 'The reproduction environment unexpectedly contains Node on PATH'}
    $TaskEntries=@([pscustomobject]@{name='platform';executable=$TaskNode})
    $TaskFound=Resolve-PocNode -RecordedEntries $TaskEntries
    if($TaskFound -ine $TaskNode){throw 'Node from an existing PoC process record was not found'}

    $TaskMissing=Join-Path $PSScriptRoot 'missing-runtime\node.exe'
    $TaskFound=Resolve-PocNode -RecordedEntries @([pscustomobject]@{name='platform';executable=$TaskMissing}) -FallbackPaths @($TaskMissing,$TaskNode)
    if($TaskFound -ine $TaskNode){throw 'A stale record must fall back to an existing Node executable'}

    $TaskRejected=$false
    try { Resolve-PocNode -FallbackPaths @((Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')) | Out-Null } catch {
        if($_.Exception.Message -notlike 'Node runtime not found*'){throw}
        $TaskRejected=$true
    }
    if(-not $TaskRejected){throw 'A different executable must not be accepted as Node'}

    $env:PATH=(Split-Path $TaskNode -Parent)+';'+$TaskSystemPath
    $TaskFound=Resolve-PocNode -RecordedEntries @([pscustomobject]@{name='platform';executable=$TaskMissing})
    if($TaskFound -ine $TaskNode){throw 'A Node already on PATH must remain usable'}

    $env:PATH=$TaskSystemPath
    $TaskVersion=& (Resolve-PocNode -FallbackPaths @($TaskNode)) --version
    if($LASTEXITCODE -ne 0 -or $TaskVersion -notmatch '^v\d+\.\d+\.\d+$'){throw 'The resolved Node must execute without changing the user PATH'}
} finally { $env:PATH=$TaskSavedPath }
Write-Output 'Node runtime resolution: 5 checks passed; no service mutation'
