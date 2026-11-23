"""Entry point for the Local AI OS V1 CLI.

This is the small, stable user-facing interface requested for V1. It delegates
to the :mod:`matlab_os.cli` package, which wraps the V1 task runner.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure the package root is importable when this entrypoint is executed directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matlab_os.cli import run_cli

if __name__ == "__main__":
    raise SystemExit(run_cli())
