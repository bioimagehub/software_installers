# convert-to-ometif

Convert microscopy image files to OME-TIFF, TIFF, NPY, or Ilastik H5.

The tool now supports two launch modes:

- Command line: pass normal arguments and it behaves like a regular CLI.
- Desktop GUI: run it with no arguments and it opens a Gooey file/folder selector interface.

## Install

```powershell
uv sync
```

Or on Windows, run the installer batch file once:

```powershell
install_convert_to_ometif.bat
```

## Run

Open the GUI:

```powershell
uv run convert-to-ometif
```

Or use the generated Start Menu shortcut, Desktop shortcut, or launcher batch file:

```powershell
convert_to_ometif.bat
```

Convert a single file from the command line:

```powershell
uv run convert-to-ometif --input-files "sample.nd2" --output-folder output
```

Convert several files directly:

```powershell
uv run convert-to-ometif --input-files "input/a.nd2;input/b.czi" --output-folder output
```

Use folder fallback recursively and limit file types:

```powershell
uv run convert-to-ometif --input-folder input --recursive --extensions .nd2,.czi --output-folder output
```

## Input options

Input selection is now file-first:

- If `--input-files` contains one or more files, those files are processed.
- If `--input-files` is empty, `--input-folder` is used and filtered by `--extensions`.

In GUI mode there is one browse control for selecting one or multiple files.

## Windows launcher files

- `convert_to_ometif.bat` launches the app in the local uv environment.
- `install_convert_to_ometif.bat` installs dependencies, creates versioned Start Menu and Desktop shortcuts, and adds a user CLI command named `convert-to-ometif`.
- `uninstall_convert_to_ometif.bat` removes the local environment, shortcuts, and CLI shim.

The installed `convert-to-ometif` command behaves the same way as the Python entrypoint:

- with arguments, it runs as a normal command-line tool
- with no arguments, it opens the GUI
