[CmdletBinding()]
param(
    [string]$OutputPath = "dist\concordance-release.zip"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$zipPath = if ([System.IO.Path]::IsPathRooted($OutputPath)) {
    [System.IO.Path]::GetFullPath($OutputPath)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputPath))
}
$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("concordance-release-" + [guid]::NewGuid().ToString("N"))
$releaseRoot = Join-Path $staging "concordance"
$sampleOutput = Join-Path $releaseRoot "dist\sample"
$env:PYTHONPATH = Join-Path $projectRoot "src"

try {
    New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null
    foreach ($relative in @("README.md", "PRODUCT.md", "AGENTS.md", "pyproject.toml")) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $relative) -Destination (Join-Path $releaseRoot $relative)
    }
    foreach ($relative in @("src", "viewer", "tools", "scripts", "fixtures")) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $relative) -Destination (Join-Path $releaseRoot $relative) -Recurse
    }

    # Keep the source-only package portable and reviewable. Local test runs can
    # leave Python bytecode, caches, or editable-install metadata in copied
    # source directories; none of those are needed by the launcher or CLI.
    Get-ChildItem -LiteralPath $releaseRoot -Recurse -Force -Directory |
        Where-Object { $_.Name -eq "__pycache__" -or $_.Name -like "*.egg-info" } |
        Sort-Object FullName -Descending |
        Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $releaseRoot -Recurse -Force -File |
        Where-Object { $_.Extension -in @(".pyc", ".pyo") } |
        Remove-Item -Force

    & python -m discord_archive validate (Join-Path $projectRoot "fixtures\sample\archive.json")
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & python -m discord_archive build (Join-Path $projectRoot "fixtures\sample\archive.json") --output $sampleOutput
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & python -m discord_archive verify $sampleOutput
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    $zipParent = Split-Path -Parent $zipPath
    New-Item -ItemType Directory -Path $zipParent -Force | Out-Null
    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }
    Compress-Archive -Path (Join-Path $releaseRoot "*") -DestinationPath $zipPath -CompressionLevel Optimal
    Write-Output "Created source-only Concordance release: $zipPath"
}
finally {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
}
