@echo off
setlocal
cd /d "%~dp0"

uv sync
if errorlevel 1 exit /b %errorlevel%

REM Read version from pyproject.toml (line: version = "x.y.z")
for /f "tokens=2 delims==" %%A in ('findstr /b /c:"version" pyproject.toml') do (
    for /f "tokens=1 delims=/" %%B in ("%%A") do set "RAW_VERSION=%%B"
)
REM Strip surrounding quotes and whitespace
set "APP_VERSION=%RAW_VERSION:"=%"
set "APP_VERSION=%APP_VERSION: =%"

set "SHORTCUT_NAME=AutoYAMLGUI %APP_VERSION%"

REM Prepare shortcut paths
set "STARTMENU_SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\%SHORTCUT_NAME%.lnk"
set "DESKTOP_SHORTCUT=%USERPROFILE%\Desktop\%SHORTCUT_NAME%.lnk"

REM Save shortcut paths for uninstaller
(echo %STARTMENU_SHORTCUT%) > shortcuts_created.txt
(echo %DESKTOP_SHORTCUT%) >> shortcuts_created.txt

echo Creating Start Menu and Desktop shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $target = (Resolve-Path '.\autoyamlgui.bat').Path; $work = (Get-Location).Path; $iconFile = Join-Path $work '.venv\Lib\site-packages\autoyamlgui\logo.ico'; $icon = if (Test-Path $iconFile) { $iconFile } else { $env:SystemRoot + '\System32\shell32.dll,220' }; $start = $ws.CreateShortcut($env:APPDATA + '\Microsoft\Windows\Start Menu\Programs\' + $env:SHORTCUT_NAME + '.lnk'); $start.TargetPath = $target; $start.WorkingDirectory = $work; $start.IconLocation = $icon; $start.Save(); $desk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\' + $env:SHORTCUT_NAME + '.lnk'); $desk.TargetPath = $target; $desk.WorkingDirectory = $work; $desk.IconLocation = $icon; $desk.Save()"

echo.
echo Start Menu shortcut created: %STARTMENU_SHORTCUT%
echo Desktop shortcut created: %DESKTOP_SHORTCUT%
echo.
echo Done. Double-click the shortcut to start capture mode,
echo or drag a YAML config file onto it to run that script.