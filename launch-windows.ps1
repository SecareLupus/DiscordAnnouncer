$ErrorActionPreference = "Stop"

function Write-Log {
  param([string]$Message)
  Write-Host "[discord-webhook-notifier] $Message"
}

function Get-PythonCommand {
  $candidates = @()
  if ($env:PYTHON) { $candidates += ,@($env:PYTHON) }
  $candidates += ,@("py", "-3")
  $candidates += ,@("python3")
  $candidates += ,@("python")

  foreach ($candidate in $candidates) {
    try {
      $args = if ($candidate.Count -gt 1) { $candidate[1..($candidate.Count - 1)] } else { @() }
      & $candidate[0] @args --version > $null 2>&1
      return $candidate
    } catch {
      continue
    }
  }

  throw "Python 3.9+ is required but was not found in PATH."
}

function Use-Python {
  param (
    [string[]]$command,
    [string[]]$args
  )

  $prefix = if ($command.Count -gt 1) { $command[1..($command.Count - 1)] } else { @() }
  & $command[0] @prefix @args
}

$pythonCommand = Get-PythonCommand
Use-Python -command $pythonCommand -args @("-c", "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)")

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
$venvPath = if ($env:VENV_DIR) { $env:VENV_DIR } else { Join-Path $root ".venv" }
$venvPython = Join-Path $venvPath "Scripts/python.exe"
$venvActivate = Join-Path $venvPath "Scripts/Activate.ps1"

if (-not (Test-Path $venvPython)) {
  Write-Log "Creating virtual environment at $venvPath"
  Use-Python -command $pythonCommand -args @("-m", "venv", $venvPath)
}

Push-Location $root
try {
  . $venvActivate
  Write-Log "Using virtual environment at $venvPath"

  python -m pip install --upgrade pip
  python -m pip install -e ".[dev]"

  python -m src.notifier.gui_tk @Args
} finally {
  Pop-Location
}
