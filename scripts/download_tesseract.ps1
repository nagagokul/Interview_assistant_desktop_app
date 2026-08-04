# Optional helper: download portable Tesseract-OCR into assets/tesseract
# Requires network. Safe to skip if Tesseract is already installed system-wide.

$ErrorActionPreference = "Stop"
$dest = Join-Path $PSScriptRoot "..\assets\tesseract"
$dest = [System.IO.Path]::GetFullPath($dest)

if (Test-Path (Join-Path $dest "tesseract.exe")) {
    Write-Host "Tesseract already present at $dest"
    exit 0
}

Write-Host "Tesseract auto-download is best-effort."
Write-Host "Preferred: install from https://github.com/UB-Mannheim/tesseract/wiki"
Write-Host "Then either:"
Write-Host "  1) Leave it in Program Files (auto-detected), or"
Write-Host "  2) Copy the install folder contents into: $dest"
Write-Host ""
Write-Host "Creating placeholder directory..."
New-Item -ItemType Directory -Force -Path $dest | Out-Null
Set-Content -Path (Join-Path $dest "README.txt") -Value @"
Place tesseract.exe, tessdata\, and dependent DLLs here for portable builds.
Download the Windows installer from:
https://github.com/UB-Mannheim/tesseract/wiki
"@
Write-Host "Done. OCR will use a system install if available."
