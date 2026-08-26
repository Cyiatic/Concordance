[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArchiveDirectory,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [switch]$IncludeMessageIndex
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$inputDirectory = (Resolve-Path -LiteralPath $ArchiveDirectory).Path
$output = if ([System.IO.Path]::IsPathRooted($OutputPath)) {
    [System.IO.Path]::GetFullPath($OutputPath)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputPath))
}
$env:PYTHONPATH = Join-Path $projectRoot "src"

$candidates = @(Get-ChildItem -LiteralPath $inputDirectory -Filter "*.json" -File | Sort-Object Name)
$archives = @()
foreach ($candidate in $candidates) {
    & python -m discord_archive validate $candidate.FullName *> $null
    if ($LASTEXITCODE -eq 0) {
        $archives += $candidate.FullName
    } else {
        Write-Warning "Skipping non-archive JSON: $($candidate.Name)"
    }
}
if (-not $archives.Count) {
    throw "No valid normalized archive JSON files were found in: $inputDirectory"
}

$arguments = @("build-catalog", "--output", $output)
foreach ($archive in $archives) {
    $arguments += @("--input", $archive)
}
if ($IncludeMessageIndex) {
    $arguments += "--include-message-index"
}

& python -m discord_archive @arguments
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
Write-Output "Indexed $($archives.Count) archive(s) from $inputDirectory"
