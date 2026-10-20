#!/usr/bin/env python3
"""Launcher: python as_installer.py <detect|recipes|check|plan|build|apply|rollback|status> ..."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from asinstall.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
