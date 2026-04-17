@echo off
setlocal
cd /d "%~dp0"

uv sync
if errorlevel 1 exit /b %errorlevel%

REM Get napari version for shortcut name (extract only version number)
for /f "tokens=3" %%V in ('uv run napari --version 2^>nul ^| findstr /i "napari version"') do set "NAPARI_VERSION=%%V"
set "SHORTCUT_NAME=Napari %NAPARI_VERSION%"

REM Prepare shortcut paths
set "STARTMENU_SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\%SHORTCUT_NAME%.lnk"
set "DESKTOP_SHORTCUT=%USERPROFILE%\Desktop\%SHORTCUT_NAME%.lnk"

REM Save shortcut paths for uninstaller
(echo %STARTMENU_SHORTCUT%) > shortcuts_created.txt
(echo %DESKTOP_SHORTCUT%) >> shortcuts_created.txt

echo Preparing Napari icon...
uv run python -c "import os; os.environ['QT_QPA_PLATFORM']='offscreen'; from PyQt6.QtGui import QGuiApplication, QIcon; app = QGuiApplication([]); icon = QIcon(r'.venv\Lib\site-packages\napari\resources\logos\gradient-plain-light.svg'); icon.pixmap(256, 256).save('napari.ico')" >nul 2>nul

echo Creating Start Menu and Desktop shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $target = (Resolve-Path '.\napari.bat').Path; $work = (Get-Location).Path; $iconFile = Join-Path $work 'napari.ico'; $icon = if (Test-Path $iconFile) { $iconFile } else { $env:SystemRoot + '\System32\shell32.dll,220' }; $start = $ws.CreateShortcut($env:APPDATA + '\Microsoft\Windows\Start Menu\Programs\' + $env:SHORTCUT_NAME + '.lnk'); $start.TargetPath = $target; $start.WorkingDirectory = $work; $start.IconLocation = $icon; $start.Save(); $desk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\' + $env:SHORTCUT_NAME + '.lnk'); $desk.TargetPath = $target; $desk.WorkingDirectory = $work; $desk.IconLocation = $icon; $desk.Save()"

echo.
echo Start Menu shortcut created: %STARTMENU_SHORTCUT%
echo Desktop shortcut created: %DESKTOP_SHORTCUT%
