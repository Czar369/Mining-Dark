"""
Local SQLite store holding the Bitcoin UTXO set.

Schema:
  addresses(address TEXT PK, satoshis INTEGER)  - every address with a balance
  meta(key TEXT PK, value TEXT)                 - metadata (date, block, source)
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mining_dark import paths

#: How long a database counts as current.  Every day past a rebuild is a day of
#: coins the scanner cannot see: at ~144 blocks a day, a month-old set - the old
#: value here - is blind to roughly 4,300 blocks of new addresses.  Six days
#: trades an hour of rebuilding for a set that is never more than a week stale.
#: Read by the messages that quote it, so the number lives in one place.
UPDATE_INTERVAL_DAYS = 6


class UTXODatabase:
    """Read/write interface to the local UTXO SQLite file."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path: Path = db_path if db_path is not None else paths.UTXO_DB_FILE
        self._conn: Optional[sqlite3.Connection] = None

    # ----- Lifecycle ---------------------------------------------------------
    def open(self) -> bool:
        """Open the database. Returns False if the file doesn't exist yet."""
        if not self.db_path.exists():
            return False
        self._conn = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
            timeout=10,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA cache_size=-65536")   # 64 MB cache
        self._conn.execute("PRAGMA temp_store=MEMORY")
        return True

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None

    def __enter__(self) -> "UTXODatabase":
        self.open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # ----- Queries -----------------------------------------------------------
    def get_balance(self, address: str) -> int:
        """Return the balance in satoshis, or 0 if the address is unknown."""
        if self._conn is None:
            return 0
        row = self._conn.execute(
            "SELECT satoshis FROM addresses WHERE address = ?", (address,)
        ).fetchone()
        return row[0] if row else 0

    # ----- Metadata ----------------------------------------------------------
    def get_meta(self, key: str, default: str = "") -> str:
        if self._conn is None:
            return default
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        if self._conn is None:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (key, value),
        )
        self._conn.commit()

    @property
    def last_updated(self) -> Optional[datetime]:
        ts = self.get_meta("last_updated")
        if not ts:
            return None
        try:
            parsed = datetime.fromisoformat(ts)
        except ValueError:
            return None
        # A timestamp written by an older build (or an external tool) can be
        # naive.  Every timestamp this scanner writes is UTC, so assume UTC for a
        # naive one rather than let it blow up the tz-aware arithmetic in
        # needs_update / age_days / status with "can't subtract offset-naive and
        # offset-aware datetimes" - a crash that reaches the GUI footer, doctor
        # and CLI, all of which read status.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    @property
    def block_height(self) -> int:
        return int(self.get_meta("block_height", "0"))

    @property
    def address_count(self) -> int:
        return int(self.get_meta("address_count", "0"))

    @property
    def source(self) -> str:
        return self.get_meta("source", "-")

    @property
    def db_size_mb(self) -> float:
        if not self.db_path.exists():
            return 0.0
        return self.db_path.stat().st_size / (1024 * 1024)

    # ----- Status ------------------------------------------------------------
    @property
    def exists(self) -> bool:
        return self.db_path.exists()

    @property
    def is_ready(self) -> bool:
        return self._conn is not None and self.address_count > 0

    @property
    def needs_update(self) -> bool:
        lu = self.last_updated
        if lu is None:
            return True
        age_days = (datetime.now(timezone.utc) - lu).days
        return age_days >= UPDATE_INTERVAL_DAYS

    @property
    def age_days(self) -> int:
        lu = self.last_updated
        if lu is None:
            return 9999
        return (datetime.now(timezone.utc) - lu).days

    @property
    def status(self) -> str:
        """'missing' | 'outdated' | 'ok'"""
        if not self.exists:
            return "missing"
        if not self.is_ready:
            return "missing"
        if self.needs_update:
            return "outdated"
        return "ok"

    # ----- Schema creation (driven by utxo_updater) --------------------------
    @staticmethod
    def _remove_with_sidecars(path: Path) -> None:
        """
        Delete a database file together with its WAL sidecars.

        SQLite removes `-wal` and `-shm` itself on a clean close, but a scanner
        that was killed leaves them behind.  Deleting only the `.db` would strip
        the database while pairing the leftovers with whatever file takes its
        name next - stale bytes next to a brand new database, and wasted space.
        """
        for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
            try:
                candidate.unlink(missing_ok=True)
            except OSError:                    # pragma: no cover - permissions
                pass

    @classmethod
    def create(cls, db_path: Optional[Path] = None) -> "UTXODatabase":
        """
        Build a fresh database at <db_path>.tmp.db.  Call finalize() to rename
        it atomically onto <db_path>.
        """
        target = db_path if db_path is not None else paths.UTXO_DB_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target.with_suffix(".tmp.db")
        cls._remove_with_sidecars(tmp_path)

        conn = sqlite3.connect(str(tmp_path))
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA cache_size=-131072")  # 128 MB while importing
        conn.execute("""
            CREATE TABLE IF NOT EXISTS addresses (
                address TEXT PRIMARY KEY,
                satoshis INTEGER NOT NULL
            ) WITHOUT ROWID
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.commit()

        db = cls(tmp_path)
        db._conn = conn
        db._final_path = target  # type: ignore[attr-defined]
        return db

    def finalize(self) -> None:
        """Close, switch back to WAL, and move the temp file into place."""
        target: Path = getattr(self, "_final_path", self.db_path)

        if self._conn is not None:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.commit()
            self._conn.close()
            self._conn = None

        if self.db_path != target:
            # os.replace() is atomic: at no point does the path stop resolving
            # to a usable database.  Deleting the target first left a window
            # where a crash meant no database at all, and rebuilding one costs
            # hours.  Only the sidecars need clearing beforehand, since the
            # rename covers just the .db itself.
            for sidecar in (Path(f"{target}-wal"), Path(f"{target}-shm")):
                try:
                    sidecar.unlink(missing_ok=True)
                except OSError:                # pragma: no cover - permissions
                    pass

            os.replace(self.db_path, target)
            self.db_path = target

    def batch_insert(self, rows: list[tuple[str, int]]) -> None:
        """Insert a batch of (address, satoshis), summing on duplicate addresses."""
        if self._conn is None:
            return
        self._conn.executemany(
            """
            INSERT INTO addresses(address, satoshis) VALUES(?, ?)
            ON CONFLICT(address) DO UPDATE SET satoshis = satoshis + excluded.satoshis
            """,
            rows,
        )

    def commit(self) -> None:
        if self._conn is not None:
            self._conn.commit()

    def count_addresses(self) -> int:
        if self._conn is None:
            return 0
        row = self._conn.execute("SELECT COUNT(*) FROM addresses").fetchone()
        return int(row[0]) if row else 0
