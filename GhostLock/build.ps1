<#
  GhostLock build script (Windows, Python 3.10+).

    .\build.ps1 -InstallPyInstaller   install PyInstaller first
    .\build.ps1                       build dist\GhostLock.exe and dist\Setup.exe
    .\build.ps1 -SetupOnly            build only dist\Setup.exe (the file you share)
    .\build.ps1 -Icons                regenerate icons first (needs: py -m pip install pillow)
#>
param(
    [switch]$InstallPyInstaller,
    [switch]$SetupOnly,
    [switch]$Icons
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($InstallPyInstaller) { py -m pip install --upgrade pyinstaller }
if ($Icons) { py tools\make_icons.py }

function Build-Exe([string]$Name) {
    py -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name $Name `
        --icon assets\ghostlock.ico `
        --version-file assets\version_info.txt `
        --add-data "assets;assets" `
        ghostlock.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $Name" }
}

if (-not $SetupOnly) { Build-Exe "GhostLock" }
Build-Exe "Setup"
Write-Host "`nDone. Output in dist\" -ForegroundColor Green
