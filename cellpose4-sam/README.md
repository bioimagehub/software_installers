# Cellpose 2D

This folder packages Cellpose for Windows using uv.

## Files
- `pyproject.toml` defines dependencies and Python requirements
- `.python-version` pins the Python version for uv
- `cellpose_2d.bat` launches the app
- `install_cellpose_2d.bat` installs dependencies and creates shortcuts

## Usage
1. Run `install_cellpose_2d.bat` once to set up the environment.
2. Launch Cellpose from the Start Menu, Desktop shortcut, or `cellpose_2d.bat`.

## Notes
- This setup is intended for older NVIDIA drivers with CUDA 11.8 PyTorch.
- The installer uses the real Cellpose icon when available.
