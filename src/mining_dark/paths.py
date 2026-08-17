"""
Central path resolution - single source of truth for every file location.

All modules import paths from here instead of hard-coding relative strings.
This makes the project location-independent (works regardless of CWD) and
lets us swap the data root via the MINING_DARK_DATA_DIR env var without
touching any code.

Layout (default):

    <project_root>/
    ├-- src/mining_dark/          ← package
    ├-- config.yaml               ← config (user-editable)
    └-- data/                     ← all runtime state
        ├-- bitcoin-core/         ← blockchain + chainstate (~15 GB)
        ├-- snapshots/            ← assumeutxo .dat files (~9 GB each)
        ├-- utxo/utxo.db          ← SQLite UTXO index (~5 GB)
        ├-- logs/                 ← rotated log files
        └-- found_wallets/        ← discovered wallets (.txt/.json/.csv)
"""

from __future__ import annotations

import os
from pathlib import Path


def _detect_project_root() -> Path:
    """
    Walk up from this file looking for the project root - the directory that
    contains BOTH `pyproject.toml` and `src/mining_dark/`.  Falls back to two
    parents up (the standard `src/mining_dark/paths.py` case).
    """
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "pyproject.toml").exists() and (parent / "src" / "mining_dark").is_dir():
            return parent
    # Fallback: <root>/src/mining_dark/paths.py -> parents[2] == <root>
    return here.parents[2]


PROJECT_ROOT: Path = _detect_project_root()

# Data root can be overridden with the MINING_DARK_DATA_DIR env var - useful for
# tests, CI, or if the user prefers to keep the 15 GB blockchain on another disk.
_DATA_ENV = os.environ.get("MINING_DARK_DATA_DIR")
DATA_DIR: Path = Path(_DATA_ENV).expanduser().resolve() if _DATA_ENV else PROJECT_ROOT / "data"

# ----- Subdirectories --------------------------------------------------------
BITCOIN_CORE_DIR: Path = DATA_DIR / "bitcoin-core"      # blockchain + chainstate
UTXO_DIR:         Path = DATA_DIR / "utxo"              # SQLite database
LOGS_DIR:         Path = DATA_DIR / "logs"              # rotated log files
FOUND_WALLETS_DIR: Path = DATA_DIR / "found_wallets"    # discovered wallets

# ----- Chainstate directories ------------------------------------------------
# Bitcoin Core keeps the UTXO set in `chainstate/`.  After `loadtxoutset`
# (assumeutxo) it creates a SECOND LevelDB in `chainstate_snapshot/` - that one
# is the active tip, while `chainstate/` keeps replaying history in the
# background.  The suffix is SNAPSHOT_CHAINSTATE_SUFFIX in Bitcoin Core's
# src/node/utxo_snapshot.h.  Use bitcoin_node.active_chainstate_dir() to pick
# the right one instead of referencing these directly.
CHAINSTATE_DIR:          Path = BITCOIN_CORE_DIR / "chainstate"
SNAPSHOT_CHAINSTATE_DIR: Path = BITCOIN_CORE_DIR / "chainstate_snapshot"

# `loadtxoutset` writes this marker only after the whole snapshot is in place.
# Its absence next to a populated chainstate_snapshot/ means the load was cut
# short - Core logs "snapshot chainstate dir is malformed" and ignores the
# directory.  It is the only on-disk signal that tells a finished load from an
# aborted one, and it works with the node stopped (which is when we dump).
SNAPSHOT_BASE_HASH_FILE: Path = SNAPSHOT_CHAINSTATE_DIR / "base_blockhash"

# ----- Well-known files ------------------------------------------------------
UTXO_DB_FILE:  Path = UTXO_DIR / "utxo.db"
UTXO_TMP_CSV:  Path = UTXO_DIR / "utxo_dump_tmp.csv"
CONFIG_FILE:   Path = PROJECT_ROOT / "config.yaml"
RPC_CREDS_FILE: Path = BITCOIN_CORE_DIR / "rpc_credentials"
SNAPSHOTS_DIR: Path = DATA_DIR / "snapshots"        # downloaded .dat UTXO snapshots


def ensure_data_dirs() -> None:
    """Create every data directory if missing.  Cheap, idempotent."""
    for d in (DATA_DIR, BITCOIN_CORE_DIR, UTXO_DIR, LOGS_DIR, FOUND_WALLETS_DIR,
              SNAPSHOTS_DIR):
        d.mkdir(parents=True, exist_ok=True)


def describe() -> str:
    """Human-readable summary of resolved paths (used by `mining-dark paths`)."""
    return (
        f"PROJECT_ROOT       = {PROJECT_ROOT}\n"
        f"DATA_DIR           = {DATA_DIR}\n"
        f"  bitcoin-core     = {BITCOIN_CORE_DIR}\n"
        f"  snapshots        = {SNAPSHOTS_DIR}\n"
        f"  utxo db          = {UTXO_DB_FILE}\n"
        f"  logs             = {LOGS_DIR}\n"
        f"  found_wallets    = {FOUND_WALLETS_DIR}\n"
        f"CONFIG_FILE        = {CONFIG_FILE}\n"
    )
