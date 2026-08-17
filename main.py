#!/usr/bin/env python3
"""
Compatibility shim - forwards to the installed `mining-dark` CLI entrypoint.

Preferred invocation after `pip install -e .`:
    mining-dark scan

Legacy invocation still supported:
    python3 main.py scan
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure `src/` is on sys.path when running from source without pip install.
_SRC = Path(__file__).resolve().parent / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mining_dark.cli import main

if __name__ == "__main__":
    main()
