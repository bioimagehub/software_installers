# Napari

This folder packages Napari for Windows using uv.

## Files
- pyproject.toml defines dependencies and Python requirements
- .python-version pins the Python version for uv
- napari.bat launches the app
- install_napari.bat installs dependencies and creates shortcuts

## Usage
1. Run install_napari.bat once to set up the environment.
2. Launch Napari from the Start Menu, Desktop shortcut, or napari.bat.

## Notes
- This setup uses an app-local uv environment.
- The installer creates a Napari icon for the Windows shortcuts when available.
