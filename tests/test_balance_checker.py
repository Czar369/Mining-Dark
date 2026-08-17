"""The save threshold is exclusive: `> min_balance`, not `>=`."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from mining_dark.checkers.balance_checker import BalanceChecker, ScanStats
from mining_dark.config.settings import Settings
from mining_dark.core.address_generator import AddressGenerator
from mining_dark.utils.utxo_db import UTXODatabase

PRIV_ONE = bytes.fromhex("00" * 31 + "01")


async def _wallets_found(tmp_path: Path, saldo: int, min_balance: int) -> int:
    """Run one known wallet through the real checker and count what it queued."""
    wallet = AddressGenerator.from_private_key(PRIV_ONE)

    db_path = tmp_path / "utxo.db"
    db = UTXODatabase.create(db_path)
    db.batch_insert([(wallet.p2pkh, saldo)])
    db.commit()
    db.set_meta("address_count", "1")
    db.finalize()
    db.close()

    settings = Settings()
    settings.scanner.address_types = ["p2pkh"]
    settings.scanner.min_balance_satoshis = min_balance

    found_queue: asyncio.Queue = asyncio.Queue()
    with UTXODatabase(db_path) as utxo_db:
        checker = BalanceChecker(
            settings=settings,
            key_queue=asyncio.Queue(),
            found_queue=found_queue,
            stats=ScanStats(),
            utxo_db=utxo_db,
        )
        await checker._process_wallet(wallet)

    return found_queue.qsize()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "saldo, min_balance, salva",
    [
        # The default.  Anything with a balance is worth keeping; zero is not,
        # or every generated key would come back as a find.  This pair is why
        # the comparison must stay exclusive - see the docstring above.
        (0, 0, False),
        (1, 0, True),
        # The threshold itself is skipped, matching config.yaml's wording
        # ("only save wallets above this balance").
        (999, 1_000, False),
        (1_000, 1_000, False),
        (1_001, 1_000, True),
    ],
)
async def test_threshold_is_exclusive(
    tmp_path: Path, saldo: int, min_balance: int, salva: bool
) -> None:
    assert (await _wallets_found(tmp_path, saldo, min_balance) == 1) is salva


@pytest.mark.asyncio
async def test_a_full_queue_is_shared_across_workers(tmp_path: Path) -> None:
    """
    The HD generator dumps a whole seed's children onto the queue at once.

    `get()` on a non-empty queue does not yield, and `_process_wallet` has no
    real await unless it finds a wallet, so without a yield point in the worker
    loop one worker drained the entire burst while the other nine sat idle -
    measured at 1 of 10 in HD mode.  Every worker must get a share.
    """
    wallet = AddressGenerator.from_private_key(PRIV_ONE)

    db_path = tmp_path / "utxo.db"
    db = UTXODatabase.create(db_path)
    db.batch_insert([("1NeverMatchesAnythingHere0000000000", 1)])
    db.commit()
    db.finalize()
    db.close()

    settings = Settings()
    settings.scanner.address_types = ["p2pkh"]

    key_queue: asyncio.Queue = asyncio.Queue()
    # Pre-fill the queue as a burst, the way the HD generator would.
    for _ in range(400):
        key_queue.put_nowait(wallet)

    per_worker: dict = {}

    def on_state(wid, status, checked):
        per_worker[wid] = checked

    with UTXODatabase(db_path) as utxo_db:
        checker = BalanceChecker(
            settings=settings, key_queue=key_queue, found_queue=asyncio.Queue(),
            stats=ScanStats(), utxo_db=utxo_db, on_worker_state=on_state,
        )
        run = asyncio.create_task(checker.run(worker_count=10))
        await key_queue.join()               # all 400 processed
        checker.stop()
        await asyncio.sleep(0.6)             # let the 0.5 s get() timeouts lapse
        run.cancel()

    working = [w for w in range(10) if per_worker.get(w, 0) > 0]
    assert len(working) >= 8, f"only {len(working)} of 10 workers shared the burst"
    assert sum(per_worker.values()) == 400


# ═══════════════════════════════════════════════════════════════════════════════
#  The session clock and the rates
# ═══════════════════════════════════════════════════════════════════════════════
#  The clock used to be plain wall-clock, and the rates divided by it.  Pausing
#  a 1,000 keys/s scan for three seconds dropped the reported rate to 250 and it
#  never recovered - measured, not theorised.

def test_the_clock_stops_while_paused() -> None:
    import time

    stats = ScanStats()
    time.sleep(0.20)
    stats.pause()

    frozen = stats.elapsed_seconds
    time.sleep(0.30)
    assert stats.elapsed_seconds == pytest.approx(frozen, abs=0.02), (
        "paused time is being counted as scanning time"
    )

    stats.resume()
    time.sleep(0.20)
    assert stats.elapsed_seconds == pytest.approx(frozen + 0.20, abs=0.05)


def test_pausing_twice_is_not_two_gaps() -> None:
    """A second PAUSE on an already paused session must not double-count."""
    import time

    stats = ScanStats()
    stats.pause()
    stats.pause()
    time.sleep(0.20)
    stats.resume()

    # Only the ~0.2 s pause was banked; elapsed is whatever ran outside it.
    assert 0.0 <= stats.elapsed_seconds < 0.10


def test_a_pause_does_not_deflate_the_rate() -> None:
    """The regression this whole section exists for."""
    import time

    stats = ScanStats()
    time.sleep(0.30)
    stats.increment(keys_generated=300)
    before = stats.keys_per_second

    stats.pause()
    time.sleep(0.60)          # twice the work time, doing nothing
    stats.resume()

    assert stats.keys_per_second == pytest.approx(before, rel=0.15), (
        "the pause was charged to the scanner as slow work"
    )


def test_the_rate_follows_the_window_not_the_whole_session(monkeypatch) -> None:
    """
    A tile reading "KEYS / S" has to mean now.

    With a lifetime average, a burst at the start kept the number high forever
    and a stall late in a long session barely moved it.
    """
    import time

    from mining_dark.checkers import balance_checker as bc

    monkeypatch.setattr(bc, "_RATE_WINDOW", 0.4)
    monkeypatch.setattr(bc, "_SAMPLE_GAP", 0.05)

    stats = ScanStats()

    # Burst: ~200 keys over ~0.2 s, polled the way the dashboard polls.
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        stats.increment(keys_generated=10)
        stats.keys_per_second
        time.sleep(0.01)
    busy = stats.keys_per_second
    assert busy > 0

    # Then the scanner stalls: keep polling, generate nothing.
    deadline = time.monotonic() + 0.6
    while time.monotonic() < deadline:
        stats.keys_per_second
        time.sleep(0.01)

    assert stats.keys_per_second == pytest.approx(0.0, abs=1.0), (
        "a stalled scanner is still reporting its old throughput"
    )
    # The lifetime average would still be ~250/s here.
    assert stats.keys_generated / stats.elapsed_seconds > 100


def test_the_rate_is_honest_before_the_window_fills() -> None:
    """The first fraction of a second has no window; it must not read zero."""
    stats = ScanStats()
    stats.increment(keys_generated=500, addresses_checked=3000)

    assert stats.keys_per_second > 0
    assert stats.checks_per_second > stats.keys_per_second
