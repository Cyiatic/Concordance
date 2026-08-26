[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path,

    [string]$OutputPath,

    [switch]$Build,

    [switch]$Open
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
$env:PYTHONPATH = Join-Path $projectRoot "src"

function Invoke-Concordance {
    param([string[]]$Arguments)
    & python -m discord_archive @Arguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$item = Get-Item -LiteralPath $resolved -ErrorAction Stop
if (-not $Build) {
    if ($item.PSIsContainer -or [System.IO.Path]::GetExtension($resolved).ToLowerInvariant() -eq ".html") {
        & (Join-Path $PSScriptRoot "open_viewer.ps1") -Path $resolved
        exit 0
    }
    throw "Use -Build with a normalized archive JSON, or provide an HTML viewer/catalog path: $Path"
}

if ($item.PSIsContainer -or [System.IO.Path]::GetExtension($resolved).ToLowerInvariant() -ne ".json") {
    throw "-Build requires a normalized archive JSON file: $Path"
}
if (-not $OutputPath) {
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($resolved)
    $OutputPath = Join-Path (Split-Path -Parent $resolved) ($stem + "-view")
}

$output = [System.IO.Path]::GetFullPath($OutputPath)
& (Join-Path $PSScriptRoot "build_archive.ps1") -ArchivePath $resolved -OutputPath $output
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
if ($Open -or -not $PSBoundParameters.ContainsKey("Open")) {
    & (Join-Path $PSScriptRoot "open_viewer.ps1") -Path $output
}
