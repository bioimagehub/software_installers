# AGENTS.md

## Purpose
This repository is for making Python programs easy to install and launch on Windows using uv.

The main goal is:
- one-click install
- one-click launch
- isolated environment per app
- simple, repeatable folder structure

## Repository Pattern
Each program should live in its own folder.


Each app folder should normally contain:
- `pyproject.toml` for dependencies and Python version requirements
- `.python-version` when Python must be pinned for uv
- a launcher `.bat` file that starts the app with uv
- an installer `.bat` file that runs setup and creates shortcuts
- an uninstaller `.bat` file that removes the environment and shortcuts (see below)
- a short `README.md`
## Uninstaller Guidance

Each app folder should provide an uninstaller batch file that:
1. Deletes the app's local uv environment (e.g., `.venv` or uv-managed folder)
2. Deletes Start Menu and Desktop shortcuts created by the installer
3. Reverses any other changes made by the installer (if applicable)
4. Does **not** delete the installer, launcher, or app source files

The uninstaller should only remove what was added or changed by the installer, leaving the app folder and its scripts intact.

Preferred pattern:

```bat
@echo off
setlocal
cd /d "%~dp0"

REM Remove uv environment
rd /s /q .venv

REM Remove Start Menu and Desktop shortcuts
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\<AppName>.lnk"
del "%USERPROFILE%\Desktop\<AppName>.lnk"

REM Add any other cleanup steps as needed
```

Examples already in this repo include separate folders for different apps.

## Expected Launcher Behavior
Launcher batch files should:
1. start in their own folder
2. use the local uv environment
3. launch the app with a simple command

Preferred pattern:

```bat
@echo off
setlocal
cd /d "%~dp0"
uv run <program>
```

## Expected Installer Behavior
Installer batch files should:
1. change into the app folder
2. run `uv sync`
3. stop on errors
4. create Start Menu shortcuts (shortcut name must include the app version after the name, e.g., "Cellpose 2D 0.1.0")
5. create Desktop shortcuts when useful (shortcut name must include the app version)
6. use the app's real icon if possible
## Shortcut Naming and Customization

All Start Menu and Desktop shortcuts must include the app version after the app name (e.g., "Cellpose 2D 0.1.0", "Napari 0.1.0").

To support multiple variants (such as 2D/3D or custom builds), the installer should make it easy to set a custom shortcut name. This can be done by defining a variable at the top of the installer batch file, e.g.:

```bat
set SHORTCUT_NAME=Cellpose 2D 0.1.0
```

or by reading from a file or argument. Use this variable for all shortcut creation commands.

This ensures that different versions or variants can coexist and be clearly identified in the Windows UI.

Preferred pattern:

```bat
@echo off
setlocal
cd /d "%~dp0"

uv sync
if errorlevel 1 exit /b %errorlevel%
```

## Icon Guidance
When creating shortcuts, look for a real app icon first.

Preferred icon sources:
1. bundled `.ico` files inside the installed package
2. app logo files inside package resources
3. a project-provided icon stored in the app folder
4. Windows default icon only as a fallback

For Python apps installed by uv, check places like:
- `.venv/Lib/site-packages/<package>/`
- resource or `logo` subfolders

If a real icon is found, wire it into both the Start Menu and Desktop shortcuts.

## Python and uv Rules
- Keep `project.requires-python` and `.python-version` aligned.
- Prefer app-local environments created by uv.
- Do not assume global Python installs.
- Keep each app self-contained in its own folder.

## AI Instructions for Future Changes
When adding a new program to this repo:
1. create a dedicated folder for that program
2. add `pyproject.toml`
3. add a launcher `.bat`
4. add an installer `.bat`
5. create shortcuts for Windows
6. search for and use a real icon if available
7. verify the launcher and shortcuts actually work

When editing existing installers, preserve the simple Windows-first workflow and avoid unnecessary complexity.
