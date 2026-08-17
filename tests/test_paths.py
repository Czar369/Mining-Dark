"""paths.py must resolve to the project root regardless of CWD."""

from __future__ import annotations

import os
from pathlib import Path


def test_project_root_contains_pyproject() -> None:
    from mining_dark import paths
    assert (paths.PROJECT_ROOT / "pyproject.toml").exists()
    assert (paths.PROJECT_ROOT / "src" / "mining_dark").is_dir()


def test_data_dir_is_absolute() -> None:
    from mining_dark import paths
    assert paths.DATA_DIR.is_absolute()
    assert paths.BITCOIN_CORE_DIR.is_absolute()
    assert paths.UTXO_DB_FILE.is_absolute()


def test_env_override(tmp_path: Path, monkeypatch) -> None:
    """MINING_DARK_DATA_DIR should relocate DATA_DIR when the module is reloaded."""
    import importlib
    monkeypatch.setenv("MINING_DARK_DATA_DIR", str(tmp_path))

    from mining_dark import paths as p
    importlib.reload(p)

    assert p.DATA_DIR == tmp_path
    assert p.UTXO_DB_FILE == tmp_path / "utxo" / "utxo.db"

    # Restore for other tests.
    monkeypatch.delenv("MINING_DARK_DATA_DIR")
    importlib.reload(p)
