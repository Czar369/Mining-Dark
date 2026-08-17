"""
End-to-end check of the live backend against a throwaway UTXO database.

This is the only test that drives the real generator/checker pipeline, so it is
also what proves the `BalanceChecker(on_worker_state=...)` hook the dashboard
depends on is actually wired up.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mining_dark.config.settings import Settings
from mining_dark.i18n import t
from mining_dark.gui.state import (
    AddressEvent,
    DatabaseEvent,
    DBStatus,
    EventBus,
    LogEvent,
    StatsEvent,
    WorkerEvent,
)
from mining_dark.utils.logger import contains_secret
from mining_dark.utils.utxo_db import UTXODatabase


def _make_db(tmp_path: Path, *, address_count: int = 1) -> Path:
    """Build a one-row UTXO database that `UTXODatabase.is_ready` accepts."""
    db_file = tmp_path / "utxo.db"
    db = UTXODatabase.create(db_file)
    db.batch_insert([("1BitcoinEaterAddressDontSendf59kuE", 1_000)])
    db.commit()
    db.set_meta("address_count", str(address_count))
    # Stamped relative to now, not a fixed date.  A literal made the fixture's
    # age drift with the calendar, and it silently changed meaning the day
    # UPDATE_INTERVAL_DAYS came down from 30 to 6 - the database this builds
    # went from "current" to "outdated" without a line of it being touched.
    db.set_meta("last_updated", (
        datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    db.set_meta("source", "test")
    db.finalize()
    return db_file


def _settings(tmp_path: Path, db_file: Path) -> Settings:
    settings = Settings()
    settings.scanner.workers = 2
    settings.scanner.queue_size = 32
    settings.scanner.address_types = ["p2pkh", "p2wpkh"]
    settings.utxo.db_file = str(db_file)
    settings.output.found_wallets_dir = str(tmp_path / "found")
    return settings


def test_live_backend_emits_telemetry(tmp_path: Path) -> None:
    from mining_dark.gui.backends.live import LiveBackend

    bus = EventBus()
    backend = LiveBackend(bus, _settings(tmp_path, _make_db(tmp_path)))

    backend.start(mode="random", workers=2)
    time.sleep(1.5)
    backend.stop(timeout=5.0)

    events = bus.drain(max_items=100_000)
    kinds = {type(e) for e in events}
    assert {StatsEvent, WorkerEvent, DatabaseEvent} <= kinds, kinds

    stats = [e for e in events if isinstance(e, StatsEvent)]
    assert stats[-1].keys_generated > 0
    assert stats[-1].addresses_checked > 0
    assert 0.0 <= stats[-1].queue_fill <= 1.0

    # The per-worker hook must actually reach the UI, for both worker slots.
    worker_ids = {e.worker_id for e in events if isinstance(e, WorkerEvent)}
    assert worker_ids == {0, 1}

    db_events = [e for e in events if isinstance(e, DatabaseEvent)]
    assert db_events[0].status is DBStatus.OK
    assert db_events[0].address_count == 1

    assert not backend.is_running


def test_live_backend_pause_stalls_the_pipeline(tmp_path: Path) -> None:
    """
    Pausing gates the key queue, so the workers idle and the counters freeze -
    without either the generator or the checker knowing a pause happened.
    """
    from mining_dark.gui.backends.live import LiveBackend

    bus = EventBus()
    backend = LiveBackend(bus, _settings(tmp_path, _make_db(tmp_path)))

    backend.start(mode="random", workers=2)
    time.sleep(0.8)

    backend.pause()
    time.sleep(0.5)                       # let the in-flight queue drain
    bus.drain(max_items=100_000)
    time.sleep(0.6)

    paused = [e for e in bus.drain(max_items=100_000) if isinstance(e, StatsEvent)]
    assert paused, "telemetry pump must keep reporting while paused"
    assert paused[-1].addresses_checked == paused[0].addresses_checked

    # The session clock has to freeze with the counters.  It did not: elapsed
    # was wall-clock, so a pause read as scanning time and - since the rates
    # divide by it - quietly deflated them for the rest of the session.
    assert paused[-1].elapsed_seconds == pytest.approx(
        paused[0].elapsed_seconds, abs=0.05
    ), "the session clock kept running while the pipeline was gated"

    backend.resume()
    time.sleep(0.6)
    resumed = [e for e in bus.drain(max_items=100_000) if isinstance(e, StatsEvent)]
    assert resumed[-1].addresses_checked > paused[-1].addresses_checked

    backend.stop(timeout=5.0)


def test_live_backend_never_publishes_key_material(tmp_path: Path) -> None:
    from mining_dark.gui.backends.live import LiveBackend

    bus = EventBus()
    backend = LiveBackend(bus, _settings(tmp_path, _make_db(tmp_path)))

    backend.start(mode="random", workers=2)
    time.sleep(1.0)
    backend.stop(timeout=5.0)

    for event in bus.drain(max_items=100_000):
        for value in (getattr(event, "message", ""), getattr(event, "address", "")):
            assert not contains_secret(value), f"secret leaked via {event!r}"


def test_live_backend_reports_a_missing_database(tmp_path: Path) -> None:
    from mining_dark.gui.backends.live import LiveBackend

    settings = _settings(tmp_path, tmp_path / "does-not-exist.db")
    bus = EventBus()
    backend = LiveBackend(bus, settings)

    backend.start(mode="random", workers=1)
    time.sleep(0.6)
    backend.stop(timeout=3.0)

    events = bus.drain(max_items=10_000)
    assert any(isinstance(e, DatabaseEvent) and e.status is DBStatus.MISSING
               for e in events)
    # Compare against the catalog rather than a literal, so the assertion holds
    # in either interface language.
    expected = t("log.db_missing")
    assert any(isinstance(e, LogEvent) and e.message == expected for e in events)


# ═══════════════════════════════════════════════════════════════════════════════
#  The recent-addresses table has to show what the scan is actually checking
# ═══════════════════════════════════════════════════════════════════════════════
#  Sampling started at the head of the list and stopped at the first hit, so of
#  the six formats being verified only address_types[0] ever reached the table:
#  measured at 498 of 498 samples, all p2pkh, while the counters proved every
#  key produced six checked addresses.

class _Wallet:
    """A wallet carrying one address per configured format."""

    p2pkh = "1PKH"
    p2pkh_uncompressed = "1UNC"
    p2sh_p2wpkh = "3SH"
    p2wpkh = "bc1qPKH"
    p2wsh = "bc1qSH"
    p2tr = "bc1pTR"


def _sampled(backend, wallet, ticks: int) -> list:
    """Drive `_on_key_generated` past its throttle `ticks` times."""
    out = []
    for _ in range(ticks):
        backend._last_address_emit = 0.0        # defeat the rate limit
        backend._on_key_generated(wallet)
    for event in backend.bus.drain(max_items=10_000):
        if isinstance(event, AddressEvent):
            out.append(event.address_type)
    return out


def test_every_configured_format_reaches_the_table(tmp_path: Path) -> None:
    from mining_dark.gui.backends.live import LiveBackend

    settings = _settings(tmp_path, _make_db(tmp_path))
    settings.scanner.address_types = [
        "p2pkh", "p2pkh_uncompressed", "p2sh_p2wpkh", "p2wpkh", "p2wsh", "p2tr",
    ]
    backend = LiveBackend(EventBus(), settings)

    seen = _sampled(backend, _Wallet(), ticks=12)

    assert set(seen) == set(settings.scanner.address_types)


def test_the_formats_are_sampled_evenly(tmp_path: Path) -> None:
    """One full turn of the cursor per pass, so no format starves."""
    from collections import Counter

    from mining_dark.gui.backends.live import LiveBackend

    settings = _settings(tmp_path, _make_db(tmp_path))
    settings.scanner.address_types = ["p2pkh", "p2wpkh", "p2tr"]
    backend = LiveBackend(EventBus(), settings)

    counts = Counter(_sampled(backend, _Wallet(), ticks=30))

    assert set(counts) == {"p2pkh", "p2wpkh", "p2tr"}
    assert set(counts.values()) == {10}


def test_a_format_the_wallet_lacks_does_not_cost_a_turn(tmp_path: Path) -> None:
    """
    An HD wallet has no uncompressed address.

    Skipping to the next format keeps the rotation from emitting nothing on
    every turn that lands on the missing one.
    """
    from mining_dark.gui.backends.live import LiveBackend

    settings = _settings(tmp_path, _make_db(tmp_path))
    settings.scanner.address_types = ["p2pkh_uncompressed", "p2wpkh"]
    backend = LiveBackend(EventBus(), settings)

    wallet = _Wallet()
    wallet.p2pkh_uncompressed = ""               # not derived for this wallet

    seen = _sampled(backend, wallet, ticks=6)

    assert seen == ["p2wpkh"] * 6


def test_no_configured_format_emits_nothing(tmp_path: Path) -> None:
    from mining_dark.gui.backends.live import LiveBackend

    settings = _settings(tmp_path, _make_db(tmp_path))
    settings.scanner.address_types = []
    backend = LiveBackend(EventBus(), settings)

    assert _sampled(backend, _Wallet(), ticks=5) == []


def test_sampling_never_publishes_key_material(tmp_path: Path) -> None:
    """The table is public addresses only - the guarantee this method carries."""
    from mining_dark.gui.backends.live import LiveBackend

    settings = _settings(tmp_path, _make_db(tmp_path))
    backend = LiveBackend(EventBus(), settings)

    class _Loaded(_Wallet):
        private_key_hex = _HEX_KEY
        wif = _WIF_KEY

    for _ in range(10):
        backend._last_address_emit = 0.0
        backend._on_key_generated(_Loaded())

    for event in backend.bus.drain(max_items=10_000):
        assert not contains_secret(getattr(event, "address", ""))


_WIF_KEY = "KwDiBf89QgGbjEhKnhXJuH7LrciVrZi3qYjgd9M7rFU73sVHnoWn"
_HEX_KEY = "4c0883a69102937d6231471b5dbb6204fe512961708279cd0d1e2d4bd63e7f4f"
