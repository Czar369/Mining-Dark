"""The balance lookup runs once per address generated, so its cost is the scan's."""

from __future__ import annotations

from pathlib import Path

import pytest

from mining_dark.checkers.local_utxo_checker import LocalUTXOChecker
from mining_dark.utils.utxo_db import UTXODatabase


@pytest.fixture
def ready_db(tmp_path: Path) -> UTXODatabase:
    db = UTXODatabase.create(tmp_path / "utxo.db")
    db.batch_insert([("1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH", 100_000)])
    db.commit()
    db.set_meta("address_count", "1")
    db.finalize()
    db.close()

    opened = UTXODatabase(tmp_path / "utxo.db")
    opened.open()
    yield opened
    opened.close()


def test_finds_a_known_balance(ready_db: UTXODatabase) -> None:
    balance = LocalUTXOChecker(ready_db).check_address(
        "1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH", "p2pkh"
    )

    assert balance is not None
    assert balance.confirmed_satoshis == 100_000


def test_unknown_address_is_zero_not_none(ready_db: UTXODatabase) -> None:
    balance = LocalUTXOChecker(ready_db).check_address("1Nope", "p2pkh")

    assert balance is not None
    assert balance.confirmed_satoshis == 0


def test_readiness_is_not_rechecked_per_address(ready_db: UTXODatabase) -> None:
    """
    is_ready is a SELECT on the meta table.  Asked once per address it cost
    more than the balance lookup it guarded - over half of the hot loop.
    """
    checker = LocalUTXOChecker(ready_db)
    calls = 0

    original = type(ready_db).is_ready.fget

    def counting(self) -> bool:
        nonlocal calls
        calls += 1
        return original(self)

    type(ready_db).is_ready = property(counting)
    try:
        for _ in range(100):
            checker.check_address("1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH", "p2pkh")
    finally:
        type(ready_db).is_ready = property(original)

    assert calls == 1, f"is_ready consultado {calls}x em 100 lookups"


def test_a_database_that_opens_later_still_works(tmp_path: Path) -> None:
    """Caching must not freeze a not-yet-ready database into never-ready."""
    db = UTXODatabase(tmp_path / "utxo.db")
    checker = LocalUTXOChecker(db)

    assert checker.check_address("1Anything", "p2pkh") is None

    built = UTXODatabase.create(tmp_path / "utxo.db")
    built.batch_insert([("1Anything", 42)])
    built.commit()
    built.set_meta("address_count", "1")
    built.finalize()
    built.close()

    db.open()
    try:
        balance = checker.check_address("1Anything", "p2pkh")
        assert balance is not None
        assert balance.confirmed_satoshis == 42
    finally:
        db.close()
