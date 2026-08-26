[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath,

    [Parameter(Mandatory = $true)]
    [string]$BundlePath
)

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$archive = (Resolve-Path -LiteralPath $ArchivePath -ErrorAction Stop).Path
$bundle = [System.IO.Path]::GetFullPath($BundlePath)
$env:PYTHONPATH = Join-Path $projectRoot "src"
$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("concordance-export-" + [guid]::NewGuid().ToString("N"))

try {
    python -m discord_archive build $archive --output $staging
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    python -m discord_archive verify $staging
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    python -m discord_archive export-bundle --input $staging --output $bundle
    exit $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
}
