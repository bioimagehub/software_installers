@echo off
setlocal
cd /d "%~dp0"
uv run python .\convert_to_tif.py %*