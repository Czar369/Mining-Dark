"""
Advisory lock that keeps a database rebuild from running under a live scan.

A rebuild renames a new file onto the database while a scanning process may
have the old one open.  The reader survives - SQLite is happy - but it holds
the deleted inode: it goes on answering from data that no longer exists, never
sees the new set, and keeps the old file's disk space allocated until it exits.
Nothing anywhere reports this, so the scan just quietly checks a stale UTXO set.

flock gives the right shape directly.  Scans take a *shared* lock, so any
number of them run side by side; a rebuild takes an *exclusive* one and is
refused while a scan holds it.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Optional

try:
    import fcntl
except ImportError:                                # pragma: no cover - Windows
    fcntl = None                                   # type: ignore[assignment]

from mining_dark import paths
from mining_dark.i18n import t


class DatabaseBusyError(RuntimeError):
    """The lock is held by someone else and the caller asked not to wait."""


def lock_path(db_file: Optional[Path] = None) -> Path:
    """The lock beside the database it protects."""
    target = db_file if db_file is not None else paths.UTXO_DB_FILE
    return Path(f"{target}.lock")


@contextmanager
def _flock(db_file: Optional[Path], operation: int, busy_message: str):
    if fcntl is None:                              # pragma: no cover - Windows
        yield
        return

    path = lock_path(db_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Closing the handle releases the lock, so the `with` is what guarantees
    # release.  The file itself stays behind on purpose: its presence never
    # means anything, which is what keeps it from going stale after a crash.
    with open(path, "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), operation | fcntl.LOCK_NB)
        except OSError:
            raise DatabaseBusyError(busy_message) from None
        yield


@contextmanager
def reading(db_file: Optional[Path] = None):
    """
    Hold the database for a scan.  Shared, so scans do not block each other.

    Taken for the whole scan rather than per query: what has to be excluded is
    a rebuild swapping the file mid-run, not individual reads.
    """
    with _flock(
        db_file,
        fcntl.LOCK_SH if fcntl is not None else 0,
        t("lock.rebuilding"),
    ):
        yield


@contextmanager
def rebuilding(db_file: Optional[Path] = None):
    """Hold the database exclusively for a rebuild.  Refused if a scan is live."""
    with _flock(
        db_file,
        fcntl.LOCK_EX if fcntl is not None else 0,
        t("lock.scanning"),
    ):
        yield
