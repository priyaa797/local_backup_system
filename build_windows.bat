@echo off
setlocal enabledelayedexpansion

echo ======================================================================
echo    Local Backup System - Windows Executable Build Script
echo ======================================================================
echo.

:: 1. Check Python installation
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python was not found in PATH.
    echo Please install Python 3.10+ from https://www.python.org/
    pause
    exit /b 1
)

echo [1/5] Verifying Python version...
python --version

:: 2. Setup Virtual Environment
if not exist "build_venv" (
    echo [2/5] Creating dedicated build virtual environment...
    python -m venv build_venv
) else (
    echo [2/5] Using existing build virtual environment...
)

call build_venv\Scripts\activate.bat

:: 3. Install build requirements
echo [3/5] Installing/Upgrading dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

:: 4. Run automated test suite before packaging
echo [4/5] Running automated verification test suite...
python -m pytest tests/
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Automated tests failed! Build aborted to prevent distributing broken binary.
    pause
    exit /b 1
)

:: 5. Package Windows executable
echo [5/5] Building Windows standalone executable with PyInstaller...
pyinstaller --clean -y local_backup_system.spec

if exist "dist\LocalBackupSystem.exe" (
    echo.
    echo ======================================================================
    echo [SUCCESS] Windows executable built successfully!
    echo Binary Location: dist\LocalBackupSystem.exe
    echo ======================================================================
    echo.
) else (
    echo.
    echo [ERROR] Build completed but dist\LocalBackupSystem.exe was not found.
    pause
    exit /b 1
)

pause
