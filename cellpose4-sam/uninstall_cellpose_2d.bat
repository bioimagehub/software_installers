@echo off
setlocal
cd /d "%~dp0"

REM Remove uv environment
rd /s /q .venv

REM Remove Start Menu and Desktop shortcuts using the list created by the installer
if exist shortcuts_created.txt (
  for /f "usebackq delims=" %%S in (shortcuts_created.txt) do del "%%S"
)

REM Add any other cleanup steps as needed
