"""
A rebuild must not swap the database out from under a running scan.

flock is held per open file description, so opening the lock twice in one
process behaves exactly like two processes - which is what these tests use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mining_dark.utils import db_lock

pytestmark = pytest.mark.skipif(
    db_lock.fcntl is None, reason="flock is not available on this platform"
)


def test_scans_do_not_block_each_other(tmp_path: Path) -> None:
    """Two readers are harmless; only a rebuild needs exclusivity."""
    db = tmp_path / "utxo.db"

    with db_lock.reading(db), db_lock.reading(db):
        pass


def test_a_rebuild_is_refused_while_a_scan_runs(tmp_path: Path) -> None:
    """
    Without this the rebuild succeeds and the scan keeps answering from the
    deleted inode - stale results, disk space never reclaimed, no warning.
    """
    db = tmp_path / "utxo.db"

    with (
        db_lock.reading(db),
        pytest.raises(db_lock.DatabaseBusyError, match="scan em andamento"),
        db_lock.rebuilding(db),
    ):
        pass


def test_a_scan_is_refused_while_a_rebuild_runs(tmp_path: Path) -> None:
    db = tmp_path / "utxo.db"

    with (
        db_lock.rebuilding(db),
        pytest.raises(db_lock.DatabaseBusyError, match="reconstru"),
        db_lock.reading(db),
    ):
        pass


def test_the_lock_is_released_on_the_way_out(tmp_path: Path) -> None:
    db = tmp_path / "utxo.db"

    with db_lock.reading(db):
        pass
    with db_lock.rebuilding(db):
        pass
    # And after an error inside the block.
    with pytest.raises(RuntimeError), db_lock.reading(db):
        raise RuntimeError("boom")
    with db_lock.rebuilding(db):
        pass


def test_the_lock_sits_beside_the_database_it_protects(tmp_path: Path) -> None:
    """Two configured databases must not share one lock."""
    first = tmp_path / "a.db"
    second = tmp_path / "b.db"

    assert db_lock.lock_path(first) != db_lock.lock_path(second)
    with db_lock.rebuilding(first), db_lock.rebuilding(second):
        pass
