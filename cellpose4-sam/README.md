# Cellpose

This folder packages Cellpose for Windows using uv.

## Files
- `pyproject.toml` defines dependencies and Python requirements
- `.python-version` pins the Python version for uv
- `cellpose.bat` launches the app
- `install_cellpose.bat` installs dependencies and creates shortcuts

## Usage
1. Run `install_cellpose.bat` once to set up the environment.
2. Launch Cellpose from the Start Menu, Desktop shortcut, `cellpose.bat`, or from any terminal with `cellpose`.
3. Open a new terminal after install before using `cellpose` (so the updated user PATH is loaded).

## Notes
- This setup is intended for older NVIDIA drivers with CUDA 11.8 PyTorch.
- The installer uses the real Cellpose icon when available.
- The `cellpose` command is added via User PATH only (not System PATH).
