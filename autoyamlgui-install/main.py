"""Thin shim so ``uv run main.py`` still works."""

import sys

from autoyamlgui.cli import main

if __name__ == "__main__":
    sys.exit(main())
