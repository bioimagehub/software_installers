# Cellpose 3 (GPU)

This folder packages Cellpose 3 for Windows using uv with CUDA 11.8 PyTorch (GPU).

## Files
- `pyproject.toml` defines dependencies and pins Cellpose to version 3
- `.python-version` pins the Python version for uv
- `cellpose3.bat` launches the app
- `install_cellpose3.bat` installs dependencies and creates shortcuts
- `uninstall_cellpose3.bat` removes the environment and shortcuts

## Usage
1. Run `install_cellpose3.bat` once to set up the environment.
2. Launch Cellpose 3 from the Start Menu, Desktop shortcut, `cellpose3.bat`, or from any terminal with `cellpose3`.
3. Open a new terminal after install before using `cellpose3` (so the updated user PATH is loaded).

## Notes
- This setup uses CUDA 11.8 PyTorch for NVIDIA GPUs.
- Cellpose 3 is pinned to the 3.x series (`cellpose[gui]==3.*`).
- The CLI command is `cellpose3` (not `cellpose`) so it can coexist with the Cellpose 4 installer in `cellpose4-sam/`.
- The installer uses the real Cellpose icon when available.
- The `cellpose3` command is added via User PATH only (not System PATH).

## Coexistence with Cellpose 4
This installer is fully independent of `cellpose4-sam/`:
- Separate uv environment (`.venv` per folder)
- Separate shortcut names (`Cellpose 3 <version>` vs `Cellpose <version>`)
- Separate CLI commands (`cellpose3` vs `cellpose`)
Both can be installed and run on the same computer at the same time.