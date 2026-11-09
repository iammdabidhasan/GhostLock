<#
  GhostLock build script (Windows, Python 3.10+).

    .\build.ps1 -InstallPyInstaller   install PyInstaller first
    .\build.ps1                       build dist\GhostLock.exe and dist\Setup.exe
    .\build.ps1 -SetupOnly            build only dist\Setup.exe (the file you share)
    .\build.ps1 -Icons                regenerate icons first (needs: py -m pip install pillow)
    .\build.ps1 -NoCamera             leave out OpenCV (smaller exe, no intruder photos)

  By default OpenCV is bundled so the optional intruder photo works. It adds roughly 40-60 MB to the exe.
#>
param(
    [switch]$InstallPyInstaller,
    [switch]$SetupOnly,
    [switch]$Icons,
    [switch]$NoCamera
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($InstallPyInstaller) { py -m pip install --upgrade pyinstaller }
if ($Icons) { py tools\make_icons.py }

$camera = @()
if ($NoCamera) {
    $camera = @("--exclude-module", "cv2", "--exclude-module", "numpy")
} else {
    py -c "import cv2" 2>$null
    if ($LASTEXITCODE -ne 0) { py -m pip install --upgrade opencv-python-headless }
    py -c "import cv2" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "OpenCV is not available. Fix pip or build with -NoCamera." }
    $camera = @("--hidden-import", "cv2", "--hidden-import", "numpy", "--collect-binaries", "cv2")
}

function Build-Exe([string]$Name) {
    py -m PyInstaller --noconfirm --clean --onefile --windowed `
        --name $Name `
        --icon assets\ghostlock.ico `
        --version-file assets\version_info.txt `
        --add-data "assets;assets" `
        @camera `
        ghostlock.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed for $Name" }
}

if (-not $SetupOnly) { Build-Exe "GhostLock" }
Build-Exe "Setup"

# Smoke test: the exe reports what it contains. Catches a build that silently lacks the camera module.
$check = if ($SetupOnly) { "dist\Setup.exe" } else { "dist\GhostLock.exe" }
$report = Join-Path $env:TEMP "ghostlock-selftest.txt"
Remove-Item $report -ErrorAction SilentlyContinue
Start-Process -FilePath $check -ArgumentList "--selftest" -Wait
if (-not (Test-Path $report)) { throw "Self-test did not run: $check failed to start." }
$text = Get-Content $report -Raw
Write-Host "`nSelf-test of ${check}:`n$text"
if (-not $NoCamera -and $text -notmatch "camera=yes") { throw "This build has no camera support. Run: py -m pip install opencv-python-headless, then build again (or use -NoCamera)." }
Write-Host "`nDone. Output in dist\" -ForegroundColor Green
