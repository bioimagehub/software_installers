@echo off
setlocal
cd /d "%~dp0"

uv sync
if errorlevel 1 exit /b %errorlevel%

REM Get cellpose version for shortcut name (extract only version number)
for /f "tokens=3" %%V in ('uv run cellpose --version 2^>nul ^| findstr /i "cellpose version"') do set "CELLPOSE_VERSION=%%V"
set "SHORTCUT_NAME=Cellpose 2D %CELLPOSE_VERSION%"

REM Prepare shortcut paths
set "STARTMENU_SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\%SHORTCUT_NAME%.lnk"
set "DESKTOP_SHORTCUT=%USERPROFILE%\Desktop\%SHORTCUT_NAME%.lnk"

REM Save shortcut paths for uninstaller
(echo %STARTMENU_SHORTCUT%) > shortcuts_created.txt
(echo %DESKTOP_SHORTCUT%) >> shortcuts_created.txt

echo Creating Start Menu and Desktop shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $target = (Resolve-Path '.\cellpose_2d.bat').Path; $work = (Get-Location).Path; $iconFile = Join-Path $work '.venv\Lib\site-packages\cellpose\logo\cellpose.ico'; $icon = if (Test-Path $iconFile) { $iconFile } else { $env:SystemRoot + '\System32\shell32.dll,220' }; $start = $ws.CreateShortcut($env:APPDATA + '\Microsoft\Windows\Start Menu\Programs\' + $env:SHORTCUT_NAME + '.lnk'); $start.TargetPath = $target; $start.WorkingDirectory = $work; $start.IconLocation = $icon; $start.Save(); $desk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\' + $env:SHORTCUT_NAME + '.lnk'); $desk.TargetPath = $target; $desk.WorkingDirectory = $work; $desk.IconLocation = $icon; $desk.Save()"

echo.
echo Start Menu shortcut created: %STARTMENU_SHORTCUT%
echo Desktop shortcut created: %DESKTOP_SHORTCUT%
