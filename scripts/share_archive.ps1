[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath,

    [switch]$Encrypt,

    [string]$PasswordFile
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$archive = (Resolve-Path -LiteralPath $ArchivePath).Path
$output = if ([System.IO.Path]::IsPathRooted($OutputPath)) {
    [System.IO.Path]::GetFullPath($OutputPath)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $OutputPath))
}

if ($Encrypt -and $PasswordFile) {
    $password = (Resolve-Path -LiteralPath $PasswordFile).Path
    if (-not (Test-Path -LiteralPath $password -PathType Leaf)) {
        throw "Password file does not exist: $PasswordFile"
    }
} elseif ($PasswordFile) {
    throw "-PasswordFile is only valid with -Encrypt"
}

if (Test-Path -LiteralPath $output) {
    throw "Output already exists; choose a new path: $output"
}

$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("concordance-safe-share-" + [guid]::NewGuid().ToString("N"))
$redactedArchive = Join-Path $staging "archive.json"
$viewer = Join-Path $staging "viewer"
$plainBundle = Join-Path $staging "safe-share.concordance.zip"
$env:PYTHONPATH = Join-Path $projectRoot "src"

function Invoke-Concordance {
    param([string[]]$Arguments)
    & python -m concordance @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Concordance command failed ($LASTEXITCODE): $($Arguments -join ' ')"
    }
}

try {
    New-Item -ItemType Directory -Path $staging -Force | Out-Null
    New-Item -ItemType Directory -Path (Split-Path -Parent $output) -Force | Out-Null

    Invoke-Concordance @(
        "redact", "--input", $archive, "--output", $redactedArchive, "--profile", "safe-share"
    )
    Invoke-Concordance @("build", $redactedArchive, "--output", $viewer)
    Invoke-Concordance @("verify", $viewer)
    Invoke-Concordance @("export-bundle", "--input", $viewer, "--output", $plainBundle)

    if ($Encrypt) {
        $arguments = @("encrypt-bundle", "--input", $plainBundle, "--output", $output)
        if ($password) {
            $arguments += @("--password-file", $password)
        }
        Invoke-Concordance $arguments
    } else {
        Copy-Item -LiteralPath $plainBundle -Destination $output
    }

    Write-Output "Created safe-share bundle: $output"
    if ($Encrypt) {
        Write-Output "Encryption: AES-256-GCM envelope"
    } else {
        Write-Output "Encryption: none; keep this bundle private or encrypt it before sharing"
    }
}
finally {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
    }
}
