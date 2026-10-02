[CmdletBinding()]
param(
    [string]$Repository = "",
    [string]$EnvFile = "",
    [string]$Command = "run.ps1"
)

$ErrorActionPreference = "Stop"
$SourceRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("crowdwisdom-clean-room-" + [guid]::NewGuid().ToString("N"))
$CloneRoot = Join-Path $TempRoot "repo"

function Stop-WithMessage([string]$Message) {
    Write-Error $Message
    exit 1
}

try {
    if ([string]::IsNullOrWhiteSpace($Repository)) {
        $Repository = (& git -C $SourceRoot remote get-url origin 2>$null)
        if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($Repository)) {
            $Repository = $SourceRoot
        }
    }
    if ([string]::IsNullOrWhiteSpace($EnvFile)) {
        $EnvFile = Join-Path $SourceRoot ".env"
    }
    if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        Stop-WithMessage "The .env file to copy was not found: $EnvFile"
    }

    New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
    Write-Host "Cloning $Repository into $CloneRoot..."
    & git clone --quiet $Repository $CloneRoot
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "git clone failed."
    }

    Copy-Item -LiteralPath $EnvFile -Destination (Join-Path $CloneRoot ".env")
    $CommandPath = Join-Path $CloneRoot $Command
    if (-not (Test-Path -LiteralPath $CommandPath -PathType Leaf)) {
        Stop-WithMessage "The requested runner was not found in the clone: $Command"
    }

    Write-Host "Running $Command in the clean clone..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $CommandPath
    exit $LASTEXITCODE
} finally {
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}
