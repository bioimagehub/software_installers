@echo off
setlocal
cd /d "%~dp0"

REM If a config file was passed (e.g. dragged onto the shortcut), run it.
REM Otherwise start capture mode.
if "%~1"=="" (
    uv run autoyamlgui --capture
) else (
    uv run autoyamlgui "%~1"
)