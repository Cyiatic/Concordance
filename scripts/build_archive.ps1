[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$archive = (Resolve-Path -LiteralPath $ArchivePath -ErrorAction Stop).Path
$output = [System.IO.Path]::GetFullPath($OutputPath)
$env:PYTHONPATH = Join-Path $projectRoot "src"

python -m discord_archive build $archive --output $output
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m discord_archive verify $output
exit $LASTEXITCODE
