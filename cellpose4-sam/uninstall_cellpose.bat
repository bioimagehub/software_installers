@echo off
setlocal
cd /d "%~dp0"

REM Remove uv environment
rd /s /q .venv

REM Remove user-scoped CLI shim and PATH entry
set "CLI_ROOT=%LOCALAPPDATA%\Programs\Cellpose"
set "CLI_BIN=%CLI_ROOT%\bin"
set "CLI_SHIM=%CLI_BIN%\cellpose.cmd"

if exist "%CLI_SHIM%" del "%CLI_SHIM%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$bin = $env:CLI_BIN; $userPath = [Environment]::GetEnvironmentVariable('Path', 'User'); if ($userPath) { $parts = $userPath -split ';' | Where-Object { $_ -ne '' -and $_.TrimEnd('\\') -ine $bin.TrimEnd('\\') }; $newPath = ($parts -join ';'); [Environment]::SetEnvironmentVariable('Path', $newPath, 'User') }"

if exist "%CLI_BIN%" rd "%CLI_BIN%" 2>nul
if exist "%CLI_ROOT%" rd "%CLI_ROOT%" 2>nul

REM Remove Start Menu and Desktop shortcuts using the list created by the installer
if exist shortcuts_created.txt (
  for /f "usebackq delims=" %%S in (shortcuts_created.txt) do del "%%S"
)

REM Fallback cleanup for both legacy and current shortcut naming schemes
powershell -NoProfile -ExecutionPolicy Bypass -Command "$paths = @($env:APPDATA + '\Microsoft\Windows\Start Menu\Programs\Cellpose *.lnk', $env:APPDATA + '\Microsoft\Windows\Start Menu\Programs\Cellpose 2D *.lnk', [Environment]::GetFolderPath('Desktop') + '\Cellpose *.lnk', [Environment]::GetFolderPath('Desktop') + '\Cellpose 2D *.lnk'); foreach ($p in $paths) { Remove-Item -Path $p -Force -ErrorAction SilentlyContinue }"

REM Add any other cleanup steps as needed
