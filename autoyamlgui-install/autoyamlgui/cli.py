"""CLI entry point for autoyamlgui."""

from __future__ import annotations

import argparse
import logging
import sys

from .loader import load_config
from .runner import Runner


def main(argv: list[str] | None = None) -> int:
    """Parse CLI args, load config, and run the automation script.

    Returns:
        Exit code: 0 on success, 1 on error.
    """
    parser = argparse.ArgumentParser(
        prog="autoyamlgui",
        description="Cross-platform GUI automation driven by YAML config files.",
    )
    parser.add_argument(
        "config",
        type=str,
        nargs="?",
        default=None,
        help="Path to the YAML config file. (Required unless --capture)",
    )
    parser.add_argument(
        "--capture",
        action="store_true",
        help="Capture mode: record mouse clicks, crop button images, and build a YAML config.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate the config only; do not execute any actions.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (debug) logging.",
    )

    args = parser.parse_args(argv)

    # Set up logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Capture mode
    if args.capture:
        from .capture import run_capture

        return run_capture()

    # Normal mode requires a config file
    if not args.config:
        parser.error("config file is required (unless using --capture)")

    # Load and validate config
    try:
        parsed = load_config(args.config)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    # Print config summary
    name = parsed.name or "unnamed"
    print(f"Script: {name}")
    print(f"Steps:  {len(parsed.steps)}")
    print(f"Button path: {parsed.environment.buttonpath}")
    print()

    if args.dry_run:
        print("Dry run — parsed steps:")
        for i, step in enumerate(parsed.steps, 1):
            print(f"  {i}. {step}")
        print("\nDry run complete. No actions executed.")
        return 0

    # Execute
    try:
        runner = Runner(parsed)
        success = runner.run()
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())