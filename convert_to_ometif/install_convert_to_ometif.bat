@echo off
setlocal
cd /d "%~dp0"

set "UV_HTTP_TIMEOUT=120"
set "SYNC_OK="

echo Resolving environment with uv...
for /L %%I in (1,1,3) do (
	echo Attempt %%I of 3: uv sync
	uv sync
	if not errorlevel 1 (
		set "SYNC_OK=1"
		goto :sync_done
	)
	echo uv sync failed on attempt %%I.
	if %%I LSS 3 (
		echo Waiting 5 seconds before retry...
		timeout /t 5 /nobreak >nul
	)
)

if not defined SYNC_OK (
	where python >nul 2>&1
	if not errorlevel 1 (
		echo Falling back to system Python: uv sync --python-preference system
		uv sync --python-preference system
		if not errorlevel 1 set "SYNC_OK=1"
	)
)

:sync_done
if not defined SYNC_OK exit /b 1

for /f "delims=" %%V in ('uv run python .\convert_to_tif.py --version 2^>nul') do set "APP_VERSION=%%V"
if not defined APP_VERSION set "APP_VERSION=convert-to-ometif 0.1.0"

set "SHORTCUT_NAME=Convert to OME-TIFF %APP_VERSION:* =%"
set "STARTMENU_SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\%SHORTCUT_NAME%.lnk"
set "DESKTOP_SHORTCUT=%USERPROFILE%\Desktop\%SHORTCUT_NAME%.lnk"

(echo %STARTMENU_SHORTCUT%) > shortcuts_created.txt
(echo %DESKTOP_SHORTCUT%) >> shortcuts_created.txt

echo Creating Start Menu and Desktop shortcuts...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $target = (Resolve-Path '.\convert_to_ometif.bat').Path; $work = (Get-Location).Path; $pythonw = Join-Path $work '.venv\Scripts\pythonw.exe'; $python = Join-Path $work '.venv\Scripts\python.exe'; $icon = if (Test-Path $pythonw) { $pythonw } elseif (Test-Path $python) { $python } else { $env:SystemRoot + '\System32\shell32.dll,220' }; $start = $ws.CreateShortcut($env:APPDATA + '\Microsoft\Windows\Start Menu\Programs\' + $env:SHORTCUT_NAME + '.lnk'); $start.TargetPath = $target; $start.WorkingDirectory = $work; $start.IconLocation = $icon; $start.Save(); $desk = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\' + $env:SHORTCUT_NAME + '.lnk'); $desk.TargetPath = $target; $desk.WorkingDirectory = $work; $desk.IconLocation = $icon; $desk.Save()"

echo Creating user-scoped CLI command...
set "CLI_ROOT=%LOCALAPPDATA%\Programs\ConvertToOMETIFF"
set "CLI_BIN=%CLI_ROOT%\bin"
set "CLI_SHIM=%CLI_BIN%\convert-to-ometif.cmd"

if not exist "%CLI_BIN%" mkdir "%CLI_BIN%"

(
	echo @echo off
	echo call "%~dp0convert_to_ometif.bat" %%*
) > "%CLI_SHIM%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$bin = $env:CLI_BIN; $userPath = [Environment]::GetEnvironmentVariable('Path', 'User'); $parts = @(); if ($userPath) { $parts = $userPath -split ';' | Where-Object { $_ -ne '' } }; $exists = $parts | Where-Object { $_.TrimEnd('\\') -ieq $bin.TrimEnd('\\') }; if (-not $exists) { $newPath = if ($userPath -and $userPath.Trim()) { $userPath.TrimEnd(';') + ';' + $bin } else { $bin }; [Environment]::SetEnvironmentVariable('Path', $newPath, 'User') }"

echo.
echo Start Menu shortcut created: %STARTMENU_SHORTCUT%
echo Desktop shortcut created: %DESKTOP_SHORTCUT%
echo CLI command created: %CLI_SHIM%
echo.
echo Run from a new Command Prompt or PowerShell window with: convert-to-ometif --help
echo Run with no arguments to open the GUI.