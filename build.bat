@echo off
REM ============================================================
REM  Interview Copilot — Windows build script (PyInstaller)
REM  Target: Intel i5 / 8GB RAM portable folder + optional EXE
REM  Run from an elevated "x64 Native Tools" OR normal cmd
REM  after installing Python 3.11+ 64-bit.
REM ============================================================
setlocal EnableDelayedExpansion

cd /d "%~dp0"

echo.
echo [1/6] Checking Python...
where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python not found on PATH. Install Python 3.11+ from python.org
  exit /b 1
)
python --version

echo.
echo [2/6] Creating virtual environment (.venv)...
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
)
call .venv\Scripts\activate.bat

echo.
echo [3/6] Installing dependencies...
python -m pip install --upgrade pip wheel setuptools
pip install -r requirements.txt
pip install pyinstaller

echo.
echo [4/6] Preparing Tesseract bundle (optional)...
if exist "assets\tesseract\tesseract.exe" (
  echo Found bundled Tesseract at assets\tesseract\
) else (
  echo NOTE: Place Tesseract-OCR binaries in assets\tesseract\ for offline OCR.
  echo       Or install system-wide from https://github.com/UB-Mannheim/tesseract/wiki
  powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\download_tesseract.ps1"
)

echo.
echo [5/6] Building portable folder with PyInstaller...
if not exist "dist" mkdir dist
pyinstaller --noconfirm --clean "packaging\InterviewCopilot.spec"
if errorlevel 1 (
  echo ERROR: PyInstaller build failed.
  exit /b 1
)

echo.
echo [6/6] Copying runtime helpers into dist...
copy /Y ".env.example" "dist\InterviewCopilot\.env.example" >nul
if exist ".env" copy /Y ".env" "dist\InterviewCopilot\.env" >nul
copy /Y "INSTALL.md" "dist\InterviewCopilot\INSTALL.md" >nul
if exist "assets\tesseract" (
  xcopy /E /I /Y "assets\tesseract" "dist\InterviewCopilot\tesseract" >nul
)

echo.
echo ============================================================
echo  BUILD COMPLETE
echo  Launch:  dist\InterviewCopilot\InterviewCopilot.exe
echo  Secrets: copy .env.example to dist\InterviewCopilot\.env
echo ============================================================
echo.
pause
endlocal
