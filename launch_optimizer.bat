@echo off
REM ─────────────────────────────────────────────────────────────
REM  360° Image Compression Optimizer — Windows Launcher
REM ─────────────────────────────────────────────────────────────
REM  Double-click this file to launch the optimizer
REM  OR drag a .jpg / .png onto this file to open it directly

echo.
echo  360^° Image Compression Optimizer
echo  ───────────────────────────────────

REM Check Python is installed
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo  [ERROR] Python not found. Install from https://python.org
    pause
    exit /b 1
)

REM Install dependencies silently if needed
echo  Checking dependencies...
python -m pip install Pillow numpy scikit-image --quiet --exists-action i

REM Launch the app (with optional drag-drop argument)
IF "%~1"=="" (
    echo  Launching...
    python "%~dp0360_optimizer.py"
) ELSE (
    echo  Opening: %~1
    python "%~dp0360_optimizer.py" "%~1"
)
