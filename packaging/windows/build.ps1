# Build Gabe for Windows: dist\windows\Gabe\Gabe.exe and the installer dist\windows\GabeSetup.exe.
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1
# Needs: Python venv with `pip install -e ".[desktop,gmail,build]"`, Node + npm, Inno Setup 6 (iscc).
# $env:PYTHON picks the interpreter (default .venv\Scripts\python.exe, else python on PATH).
# $env:SIGN_CERT / $env:SIGN_PASSWORD (a .pfx) sign Gabe.exe and the installer with signtool.
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path "$PSScriptRoot\..\..").Path
Set-Location $Root
$Out = "$Root\dist\windows"
$Version = if ($env:GABE_VERSION) { $env:GABE_VERSION } else { "0.1.0" }

$Py = $env:PYTHON
if (-not $Py) { $Py = if (Test-Path "$Root\.venv\Scripts\python.exe") { "$Root\.venv\Scripts\python.exe" } else { "python" } }

function Run($exe, [string[]]$argv) {
  & $exe @argv
  if ($LASTEXITCODE -ne 0) { throw "$exe failed ($LASTEXITCODE)" }
}

if (-not (Test-Path "packaging\google_oauth_client.json")) {
  Write-Warning "packaging\google_oauth_client.json is missing; Sign in with Google won't work in this build."
}

Write-Host "-> build stamp"
$Commit = if ($env:GABE_COMMIT) { $env:GABE_COMMIT } else { (git rev-parse HEAD 2>$null) }
Run $Py @("-m", "gabe.updater", "stamp", "$Commit")  # gabe\_build.py: the commit the updater compares with GitHub

Write-Host "-> front end"
Push-Location web
try { Run npm @("ci", "--silent"); Run npm @("run", "build", "--silent") } finally { Pop-Location }

Write-Host "-> app"
Run $Py @("-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
  "--distpath", $Out, "--workpath", "$Root\packaging\build\pyinstaller-win", "packaging\windows\Gabe.spec")

function Sign($file) {
  if (-not $env:SIGN_CERT) { return }
  $signtool = (Get-Command signtool.exe -ErrorAction SilentlyContinue).Source
  if (-not $signtool) {
    $signtool = (Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
      Sort-Object FullName -Descending | Select-Object -First 1).FullName
  }
  if (-not $signtool) { throw "SIGN_CERT is set but signtool.exe wasn't found" }
  Run $signtool @("sign", "/f", $env:SIGN_CERT, "/p", $env:SIGN_PASSWORD, "/fd", "sha256",
    "/tr", "http://timestamp.digicert.com", "/td", "sha256", $file)
}
Sign "$Out\Gabe\Gabe.exe"

Write-Host "-> installer"
$Iscc = Get-Command iscc.exe -ErrorAction SilentlyContinue
$IsccPath = if ($Iscc) { $Iscc.Source } else {
  @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $IsccPath) { throw "Inno Setup 6 not found (winget install JRSoftware.InnoSetup)" }
Run $IsccPath @("/Q", "/DAppVersion=$Version", "/DSourceDir=$Out\Gabe", "/DOutDir=$Out", "packaging\windows\Gabe.iss")
Sign "$Out\GabeSetup.exe"

Write-Host "Done: $Out\Gabe\Gabe.exe"
Write-Host "      $Out\GabeSetup.exe"
