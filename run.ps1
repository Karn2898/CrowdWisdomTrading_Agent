[CmdletBinding()]
param(
    [string]$VenvDir = ".venv",
    [string]$PythonCommand = "py"
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $RootDir

$RequiredKeys = @(
    "APIFY_TOKEN",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "TAVILY_API_KEY",
    "EXA_API_KEY"
)

function Stop-WithMessage([string]$Message) {
    Write-Error $Message
    exit 1
}

try {
    & $PythonCommand --version *> $null
} catch {
    Stop-WithMessage "Python was not found. Install Python 3.10+ and ensure '$PythonCommand' is available."
}

$VenvPython = Join-Path $RootDir (Join-Path $VenvDir "Scripts\python.exe")
if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    Write-Host "Creating virtual environment in $VenvDir..."
    & $PythonCommand -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "Could not create the virtual environment."
    }
}

Write-Host "Installing Python requirements..."
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage "Could not upgrade pip."
}
& $VenvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage "Could not install requirements.txt."
}

$EnvFile = Join-Path $RootDir ".env"
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    Stop-WithMessage "Missing .env. Copy .env.example to .env and fill in the required API keys."
}

$EnvValues = @{}
foreach ($Line in Get-Content -LiteralPath $EnvFile) {
    if ($Line -match '^\s*#' -or $Line -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
        continue
    }
    $Value = $Matches[2].Trim()
    if ($Value.Length -ge 2 -and $Value.StartsWith('"') -and $Value.EndsWith('"')) {
        $Value = $Value.Substring(1, $Value.Length - 2)
    }
    $EnvValues[$Matches[1]] = $Value
}

$MissingKeys = @($RequiredKeys | Where-Object {
    -not $EnvValues.ContainsKey($_) -or [string]::IsNullOrWhiteSpace($EnvValues[$_])
})
if ($MissingKeys.Count -gt 0) {
    Stop-WithMessage ("Missing or empty required .env keys: {0}`nCopy .env.example to .env and set each value before retrying." -f ($MissingKeys -join ", "))
}

if (-not (Get-Command hermes -ErrorAction SilentlyContinue)) {
    Stop-WithMessage "The 'hermes' CLI is missing. Install Hermes and ensure it is on PATH."
}
if (-not (Test-Path -LiteralPath (Join-Path $RootDir "pipeline.py") -PathType Leaf)) {
    Stop-WithMessage "pipeline.py was not found in the repository root."
}

Write-Host "Running the CrowdWisdom pipeline..."
& $VenvPython (Join-Path $RootDir "pipeline.py")
exit $LASTEXITCODE
