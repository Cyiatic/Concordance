[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

$resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
$item = Get-Item -LiteralPath $resolved -ErrorAction Stop
if ($item.PSIsContainer) {
    $target = Join-Path $item.FullName "index.html"
} else {
    $target = $item.FullName
}

if (-not (Test-Path -LiteralPath $target -PathType Leaf) -or [System.IO.Path]::GetExtension($target).ToLowerInvariant() -ne ".html") {
    throw "Viewer path must be an existing HTML file or a directory containing index.html: $Path"
}

Start-Process -FilePath $target
