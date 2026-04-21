@echo off
setlocal enabledelayedexpansion

REM Check if UV is already installed
echo Checking if UV is already installed...
uv --version >nul 2>&1
if %errorlevel% equ 0 (
    echo UV is already installed!
    uv --version
    pause
    exit /b 0
)

REM Define installation directory (user-specific)
set "INSTALL_DIR=%LOCALAPPDATA%\uv"
set "UV_VERSION=latest"

REM Create installation directory if it doesn't exist
if not exist "%INSTALL_DIR%" (
    mkdir "%INSTALL_DIR%"
    echo Created directory: %INSTALL_DIR%
)

REM Download UV (using PowerShell for better reliability)
echo Downloading UV...
powershell -Command "& {$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri 'https://astral.sh/uv/install.ps1' -OutFile '%TEMP%\uv_install.ps1'}"

REM Run the UV installer with user-specific path
echo Installing UV to %INSTALL_DIR%...
powershell -ExecutionPolicy Bypass -File "%TEMP%\uv_install.ps1" -InstallDir "%INSTALL_DIR%"

REM Add UV to user PATH if not already present
for /f "tokens=2*" %%A in ('reg query "HKEY_CURRENT_USER\Environment" /v PATH 2^>nul') do set "CURRENT_PATH=%%B"

if not defined CURRENT_PATH set "CURRENT_PATH="

echo Checking if UV is already in PATH...
echo %CURRENT_PATH% | find /i "%INSTALL_DIR%" >nul
if errorlevel 1 (
    echo Adding UV to user PATH...
    setx PATH "%CURRENT_PATH%;%INSTALL_DIR%"
    echo Successfully added UV to PATH!
) else (
    echo UV is already in PATH.
)

echo.
echo Installation complete! Please restart your terminal or command prompt for changes to take effect.
pause
