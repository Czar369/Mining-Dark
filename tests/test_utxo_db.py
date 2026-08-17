"""Rebuilding the UTXO database must never leave the scanner without one."""

from __future__ import annotations

import os
from pathlib import Path

from mining_dark.utils.utxo_db import UTXODatabase


def _build(target: Path, address: str, satoshis: int) -> None:
    db = UTXODatabase.create(target)
    db.batch_insert([(address, satoshis)])
    db.commit()
    db.set_meta("address_count", "1")
    db.finalize()
    db.close()


def test_rebuild_replaces_the_data(tmp_path: Path) -> None:
    target = tmp_path / "utxo.db"
    _build(target, "velho", 1)
    _build(target, "novo", 2)

    with UTXODatabase(target) as db:
        assert db.get_balance("novo") == 2
        assert db.get_balance("velho") == 0


def test_the_database_never_stops_existing(tmp_path: Path, monkeypatch) -> None:
    """
    finalize() used to unlink the target before renaming onto it.  A crash in
    that window left no database at all, and rebuilding one costs hours.
    """
    target = tmp_path / "utxo.db"
    _build(target, "velho", 1)

    original = os.replace
    seen: list[bool] = []

    def watching(src, dst, *args, **kwargs):
        # The old file must still be in place when the swap happens.
        seen.append(Path(dst).exists())
        return original(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", watching)
    _build(target, "novo", 2)

    assert seen == [True], "o alvo foi apagado antes do rename"
    assert target.exists()


def test_stale_sidecars_do_not_survive_a_rebuild(tmp_path: Path) -> None:
    """A -wal from the previous build paired with a new database is corruption."""
    target = tmp_path / "utxo.db"
    _build(target, "velho", 1)
    Path(f"{target}-wal").write_bytes(b"lixo da build anterior")
    Path(f"{target}-shm").write_bytes(b"lixo da build anterior")

    _build(target, "novo", 2)

    assert not Path(f"{target}-wal").exists()
    assert not Path(f"{target}-shm").exists()
    with UTXODatabase(target) as db:
        assert db.get_balance("novo") == 2


# ═══════════════════════════════════════════════════════════════════════════════
#  How long a database counts as current
# ═══════════════════════════════════════════════════════════════════════════════
def _aged(tmp_path, days: float):
    """A ready database whose last rebuild was `days` ago."""
    from datetime import datetime, timedelta, timezone

    from mining_dark.utils.utxo_db import UTXODatabase

    path = tmp_path / "utxo.db"
    db = UTXODatabase.create(path)
    db.batch_insert([("1BitcoinEaterAddressDontSendf59kuE", 1_000)])
    db.commit()
    db.set_meta("address_count", "1")
    db.set_meta("last_updated",
                (datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
    db.finalize()
    db.close()
    return path


def test_a_database_inside_the_interval_is_current(tmp_path) -> None:
    from mining_dark.utils.utxo_db import UPDATE_INTERVAL_DAYS, UTXODatabase

    with UTXODatabase(_aged(tmp_path, UPDATE_INTERVAL_DAYS - 1)) as db:
        assert db.needs_update is False
        assert db.status == "ok"


def test_a_database_at_the_interval_is_outdated(tmp_path) -> None:
    """
    The boundary is inclusive: at exactly the interval it wants a rebuild.

    Every day past one is a day of coins the scanner cannot see - about 144
    blocks of new addresses - so the benefit of the doubt goes to rebuilding.
    """
    from mining_dark.utils.utxo_db import UPDATE_INTERVAL_DAYS, UTXODatabase

    with UTXODatabase(_aged(tmp_path, UPDATE_INTERVAL_DAYS)) as db:
        assert db.needs_update is True
        assert db.status == "outdated"


def test_a_database_that_never_recorded_a_rebuild_wants_one(tmp_path) -> None:
    from mining_dark.utils.utxo_db import UTXODatabase

    path = tmp_path / "utxo.db"
    db = UTXODatabase.create(path)
    db.batch_insert([("1BitcoinEaterAddressDontSendf59kuE", 1_000)])
    db.commit()
    db.finalize()
    db.close()

    with UTXODatabase(path) as db:
        assert db.needs_update is True


def test_a_naive_timestamp_does_not_crash_status(tmp_path) -> None:
    """
    An older build wrote last_updated with a naive datetime.  Subtracting it from
    an aware now() raised TypeError inside needs_update / age_days / status - a
    crash that reached the GUI footer, doctor and CLI.  A naive value is now read
    as UTC instead of blowing up.
    """
    from datetime import datetime

    path = tmp_path / "utxo.db"
    db = UTXODatabase.create(path)
    db.batch_insert([("1BitcoinEaterAddressDontSendf59kuE", 1_000)])
    db.commit()
    db.set_meta("address_count", "1")
    # No tzinfo - exactly what datetime.utcnow().isoformat() would have stored.
    db.set_meta("last_updated", datetime(2026, 8, 1, 12, 0, 0).isoformat())
    db.finalize()
    db.close()

    with UTXODatabase(path) as db:
        assert db.last_updated is not None
        assert db.last_updated.tzinfo is not None      # normalised to aware UTC
        assert isinstance(db.needs_update, bool)       # no TypeError
        assert isinstance(db.age_days, int)
        assert db.status in {"ok", "outdated"}


def test_the_messages_quote_the_configured_interval() -> None:
    """
    Three places used to spell "30 days" by hand.

    Lowering the constant left all three lying, in two languages, with nothing
    to catch it - so they read it now instead of repeating it.
    """
    import pathlib

    from mining_dark.utils.utxo_db import UPDATE_INTERVAL_DAYS

    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "mining_dark"
    stale = [
        path.relative_to(src)
        for path in src.rglob("*.py")
        if "30 dias" in path.read_text() or "30 days" in path.read_text()
    ]

    assert stale == []
    assert UPDATE_INTERVAL_DAYS == 6
