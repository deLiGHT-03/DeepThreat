$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".build-venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    $pythonExe = $env:DEEPTHREAT_PYTHON
    if (-not $pythonExe) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) { $pythonCommand = Get-Command py -ErrorAction SilentlyContinue }
        if (-not $pythonCommand) { throw "Set DEEPTHREAT_PYTHON to Python 3.10+ or install Python and add it to PATH." }
        $pythonExe = $pythonCommand.Source
    }
    if ([IO.Path]::GetFileName($pythonExe) -eq "py.exe" -or [IO.Path]::GetFileName($pythonExe) -eq "py") {
        & $pythonExe -3 -m venv $venvPath
    } else {
        & $pythonExe -m venv $venvPath
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not create the packaging environment." }
}

& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $projectRoot "requirements.txt") pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Could not install the build dependencies." }

Push-Location $projectRoot
try {
    & $venvPython -m PyInstaller --noconfirm --clean (Join-Path $PSScriptRoot "DeepThreat.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller did not complete successfully." }

    $compilerPath = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
    if (-not $compilerPath) {
        $compilerCandidates = @(
            (Join-Path $projectRoot ".build-tools\InnoSetup\ISCC.exe"),
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
        )
        $compilerPath = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    }
    if ($compilerPath) {
        & $compilerPath (Join-Path $PSScriptRoot "DeepThreat.iss")
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup did not complete successfully." }
        Write-Host "Installer created at release\DeepThreatSetup.exe"
    } else {
        Write-Warning "Inno Setup 6 is not installed. The runnable app folder is at dist\DeepThreat; install Inno Setup 6 and rerun to create the installer."
    }
} finally {
    Pop-Location
}
