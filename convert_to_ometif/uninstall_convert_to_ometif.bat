@echo off
setlocal
cd /d "%~dp0"

rd /s /q .venv

set "CLI_ROOT=%LOCALAPPDATA%\Programs\ConvertToOMETIFF"
set "CLI_BIN=%CLI_ROOT%\bin"
set "CLI_SHIM=%CLI_BIN%\convert-to-ometif.cmd"

if exist "%CLI_SHIM%" del "%CLI_SHIM%"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$bin = $env:CLI_BIN; $userPath = [Environment]::GetEnvironmentVariable('Path', 'User'); if ($userPath) { $parts = $userPath -split ';' | Where-Object { $_ -ne '' -and $_.TrimEnd('\\') -ine $bin.TrimEnd('\\') }; $newPath = ($parts -join ';'); [Environment]::SetEnvironmentVariable('Path', $newPath, 'User') }"

if exist "%CLI_BIN%" rd "%CLI_BIN%" 2>nul
if exist "%CLI_ROOT%" rd "%CLI_ROOT%" 2>nul

if exist shortcuts_created.txt (
  for /f "usebackq delims=" %%S in (shortcuts_created.txt) do del "%%S"
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$paths = @($env:APPDATA + '\Microsoft\Windows\Start Menu\Programs\Convert to OME-TIFF *.lnk', [Environment]::GetFolderPath('Desktop') + '\Convert to OME-TIFF *.lnk'); foreach ($p in $paths) { Remove-Item -Path $p -Force -ErrorAction SilentlyContinue }"