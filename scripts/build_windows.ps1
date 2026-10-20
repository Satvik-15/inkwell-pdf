# Builds dist\LocalPDF\ (the app) and dist\LocalPDF-<version>-Windows-Setup.exe on Windows.
# Needs Python 3.10+ and Inno Setup 6 (https://jrsoftware.org/isinfo.php, or: choco install innosetup).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
$Version = (Select-String -Path "localpdf\__init__.py" -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value

python -m venv .build-venv
.\.build-venv\Scripts\python -m pip install -q --upgrade pip
.\.build-venv\Scripts\python -m pip install -q -r requirements.txt pyinstaller pillow

# Windows icon from the shared PNG
if (-not (Test-Path dist)) { New-Item -ItemType Directory dist | Out-Null }
.\.build-venv\Scripts\python -c "from PIL import Image; Image.open('assets/icon.png').save('dist/LocalPDF.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"

.\.build-venv\Scripts\pyinstaller --noconfirm --clean --windowed --name LocalPDF `
  --icon dist\LocalPDF.ico `
  --add-data "localpdf\web;localpdf\web" `
  --add-data "localpdf\fonts;localpdf\fonts" `
  --collect-all webview `
  --hidden-import clr `
  run_localpdf.py

$Iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe", "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) { throw "Inno Setup 6 not found. Install it from https://jrsoftware.org/isinfo.php" }
& $Iscc "/DAppVersion=$Version" "scripts\windows\LocalPDF.iss"
Write-Host "Built dist\LocalPDF-$Version-Windows-Setup.exe"
