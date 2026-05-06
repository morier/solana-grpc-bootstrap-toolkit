#!/usr/bin/env python3
"""Backward-compatible wrapper for the installable package entrypoint."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from solana_grpc_bootstrap_toolkit.cli import *  # noqa: F401,F403
from solana_grpc_bootstrap_toolkit.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
