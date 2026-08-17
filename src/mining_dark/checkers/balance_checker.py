"""
Drives the balance lookups for a scan session.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Callable, Optional

from loguru import logger

from mining_dark.checkers.local_utxo_checker import LocalUTXOChecker
from mining_dark.config.settings import Settings
from mining_dark.core.wallet import FoundWallet, WalletBalance, WalletKeys
from mining_dark.utils.utxo_db import UTXODatabase


#: Window the live rates are measured over.  Long enough that the 0.1 s
#: telemetry tick does not read as jitter, short enough that the number still
#: describes what the scanner is doing now rather than half an hour ago.
_RATE_WINDOW = 5.0

#: Smallest gap between snapshots.  Without it a fast reader would fill the
#: ring with samples spanning no measurable time, and the rate would be a
#: division of two nearly equal numbers by nearly zero.
_SAMPLE_GAP = 0.25


class ScanStats:
    """
    Counters shared by every task in a scan.  asyncio is single-threaded, so
    plain attribute updates are safe without a lock.

    Two things here are deliberately not the obvious implementation:

    `elapsed_seconds` excludes time spent paused.  It used to be plain
    wall-clock, which meant a pause kept the clock running while nothing was
    being scanned - and since the rates divide by it, a three second pause on
    a thousand keys/s scan dropped the reported rate to 250 and it never
    recovered.

    The rates are measured over the last few seconds rather than the whole
    session.  A tile that says "KEYS / S" is read as "right now"; a lifetime
    average bakes in the start-up ramp forever and barely moves when the
    scanner slows down hours in.
    """

    __slots__ = (
        "_paused_at",
        "_paused_total",
        "_samples",
        "addresses_checked",
        "keys_generated",
        "started_at",
        "total_found_satoshis",
        "wallets_found",
    )

    def __init__(self) -> None:
        self.keys_generated: int = 0
        self.addresses_checked: int = 0
        self.wallets_found: int = 0
        self.total_found_satoshis: int = 0
        self.started_at: float = time.monotonic()

        # Monotonic instant the current pause began, None while running.
        self._paused_at: Optional[float] = None
        self._paused_total: float = 0.0
        # (elapsed, keys_generated, addresses_checked), oldest first.
        self._samples: deque = deque()

    def increment(self, **kwargs: int) -> None:
        for k, v in kwargs.items():
            setattr(self, k, getattr(self, k) + v)

    # ----- pause bookkeeping -------------------------------------------------
    def pause(self) -> None:
        """Stop the clock.  Idempotent - a second pause is not a second gap."""
        if self._paused_at is None:
            self._paused_at = time.monotonic()

    def resume(self) -> None:
        """Restart the clock, banking however long the pause lasted."""
        if self._paused_at is not None:
            self._paused_total += time.monotonic() - self._paused_at
            self._paused_at = None

    @property
    def is_paused(self) -> bool:
        return self._paused_at is not None

    # ----- derived -----------------------------------------------------------
    @property
    def elapsed_seconds(self) -> float:
        """Seconds actually spent scanning - paused time does not count."""
        paused = self._paused_total
        if self._paused_at is not None:
            paused += time.monotonic() - self._paused_at
        return time.monotonic() - self.started_at - paused

    def _observe(self) -> None:
        """
        Record where the counters are now, and drop what fell out of the window.

        Driven by reads rather than by an explicit call from each publisher:
        both the dashboard and the GUI telemetry pump poll the rates several
        times a second, so the ring stays fresh on its own, and a consumer that
        forgets to sample cannot silently freeze the number.

        While paused `elapsed_seconds` does not advance, so this appends
        nothing and the window survives the pause intact - which is exactly
        what makes the rate pick up where it left off.
        """
        now = self.elapsed_seconds
        if self._samples and now - self._samples[-1][0] < _SAMPLE_GAP:
            return

        self._samples.append((now, self.keys_generated, self.addresses_checked))
        cutoff = now - _RATE_WINDOW
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def _rate(self, index: int) -> float:
        self._observe()

        if len(self._samples) < 2:
            # Too early to have a window - the lifetime average is the honest
            # answer for the first fraction of a second, and equals the windowed
            # one anyway while the session is younger than the window.
            elapsed = self.elapsed_seconds
            total = self.keys_generated if index == 1 else self.addresses_checked
            return total / elapsed if elapsed > 0 else 0.0

        oldest, newest = self._samples[0], self._samples[-1]
        span = newest[0] - oldest[0]
        return (newest[index] - oldest[index]) / span if span > 0 else 0.0

    @property
    def keys_per_second(self) -> float:
        return self._rate(1)

    @property
    def checks_per_second(self) -> float:
        return self._rate(2)


class BalanceChecker:
    """
    N async workers pull from key_queue, check every configured address type
    against the local UTXO set, and push anything with a balance onto
    found_queue.
    """

    def __init__(
        self,
        settings: Settings,
        key_queue: "asyncio.Queue[WalletKeys]",
        found_queue: "asyncio.Queue[FoundWallet]",
        stats: ScanStats,
        utxo_db: UTXODatabase,
        on_wallet_found: Optional[Callable[[FoundWallet], None]] = None,
        on_worker_state: Optional[Callable[[int, str, int], None]] = None,
    ) -> None:
        self._settings = settings
        self._key_queue = key_queue
        self._found_queue = found_queue
        self._stats = stats
        self._on_found: Optional[Callable[[FoundWallet], None]] = on_wallet_found
        # Optional per-worker telemetry: (worker_id, status, wallets_processed).
        # Used by the graphical dashboard; None in headless/CLI runs.
        self._on_worker_state: Optional[Callable[[int, str, int], None]] = on_worker_state
        self._local = LocalUTXOChecker(utxo_db)
        self._address_types: list[str] = settings.scanner.address_types
        self._min_balance = settings.scanner.min_balance_satoshis
        self._running = False

    def stop(self) -> None:
        self._running = False

    async def run(self, worker_count: int) -> None:
        self._running = True
        workers = [
            asyncio.create_task(self._worker(i), name=f"worker-{i}")
            for i in range(worker_count)
        ]
        try:
            await asyncio.gather(*workers, return_exceptions=True)
        finally:
            self._running = False

    async def _worker(self, worker_id: int) -> None:
        processed = 0

        while self._running:
            try:
                wallet = await asyncio.wait_for(self._key_queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                # Not "nothing to do" - the queue had no key to hand over, so
                # the generator is behind.  Reported as WAITING because that is
                # what the dashboard needs to distinguish from a stopped run.
                self._report(worker_id, "WAITING", processed)
                continue
            except asyncio.CancelledError:
                return

            try:
                self._report(worker_id, "VERIFYING", processed)
                await self._process_wallet(wallet)
                processed += 1
                self._report(worker_id, "SCANNING", processed)
            except asyncio.CancelledError:
                # The `finally` below already balances this get(); calling
                # task_done() here too raised "called too many times", which
                # gather(return_exceptions=True) then swallowed - leaving the
                # key in hand discarded without ever being checked.
                return
            except Exception as exc:
                logger.debug(f"worker {worker_id} failed: {exc}")
            finally:
                self._key_queue.task_done()

            # Hand control back to the loop, so a queue that is already full -
            # which is what the HD generator produces, dumping a whole seed's
            # children at once - is shared round-robin instead of drained by
            # this one worker.  `get()` on a non-empty queue does not yield, and
            # `_process_wallet` has no real await unless it finds a wallet, so
            # without this a single worker did all the work while the other
            # nine sat idle (measured: 1 of 10 in HD mode).  In random mode the
            # queue rarely holds more than one key, so this changes nothing.
            await asyncio.sleep(0)

    def _report(self, worker_id: int, status: str, processed: int) -> None:
        """Fire the optional telemetry hook.  A broken observer never stops a scan."""
        if self._on_worker_state is None:
            return
        try:
            self._on_worker_state(worker_id, status, processed)
        except Exception as exc:                                  # noqa: BLE001
            logger.debug(f"worker telemetry hook failed: {exc}")

    async def _process_wallet(self, wallet: WalletKeys) -> None:
        addresses = [
            (addr, atype)
            for atype, addr in wallet.all_addresses.items()
            if atype in self._address_types and addr
        ]

        positive_balances: list[WalletBalance] = []

        for address, address_type in addresses:
            balance = self._local.check_address(address, address_type)
            self._stats.increment(addresses_checked=1)

            if balance and balance.total_satoshis > self._min_balance:
                positive_balances.append(balance)

        if positive_balances:
            found = FoundWallet(keys=wallet, balances=positive_balances)
            await self._found_queue.put(found)
            self._stats.increment(
                wallets_found=1,
                total_found_satoshis=found.total_confirmed_satoshis,
            )
            if self._on_found:
                self._on_found(found)
            logger.success(
                f"WALLET ENCONTRADA | {found.primary_address} | "
                f"{found.total_confirmed_satoshis} sat"
            )
