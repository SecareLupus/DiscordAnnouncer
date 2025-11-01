from __future__ import annotations

import sys

from .notifier.cli import run_cli


def run() -> None:
    """Entry point for console_scripts."""
    exit_code = run_cli()
    raise SystemExit(exit_code)


if __name__ == "__main__":
    run()
