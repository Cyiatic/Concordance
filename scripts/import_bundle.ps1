[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BundlePath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [switch]$Open
)

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$bundle = (Resolve-Path -LiteralPath $BundlePath -ErrorAction Stop).Path
$output = [System.IO.Path]::GetFullPath($OutputPath)
$env:PYTHONPATH = Join-Path $projectRoot "src"

python -m concordance import-bundle --input $bundle --output $output
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ($Open) {
    & (Join-Path $PSScriptRoot "open_viewer.ps1") -Path (Join-Path $output "index.html")
}
