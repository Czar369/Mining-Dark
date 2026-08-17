"""
Live backend - runs the real Mining-Dark scanner behind the GUI.

This is deliberately a thin adapter.  It reuses `RandomKeyGenerator`,
`HDWalletGenerator`, `BalanceChecker`, `UTXODatabase` and `FileManager`
unchanged, exactly as `cli._run_scan` does, and only replaces the Rich
dashboard with `EventBus` publications.

Threading: the whole asyncio scanner lives on this backend's thread inside its
own event loop.  Nothing here touches Dear PyGui.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import ExitStack
from typing import Any, Optional

from mining_dark.gui.backends.base import ScanBackend
from mining_dark.i18n import t
from mining_dark.gui.state import (
    AddressEvent,
    DatabaseEvent,
    DBStatus,
    FoundEvent,
    LogLevel,
    StatsEvent,
    WorkerEvent,
    WorkerStatus,
)

# Publication throttles - the scanner runs orders of magnitude faster than the
# screen refreshes, so telemetry is sampled rather than streamed.
_STATS_PERIOD = 0.10       # seconds between StatsEvent
_WORKER_PERIOD = 0.05      # seconds between WorkerEvent, per worker
_ADDRESS_PERIOD = 0.07     # seconds between AddressEvent samples
_DB_PERIOD = 15.0          # seconds between DatabaseEvent refreshes


class _PausableQueue(asyncio.Queue):
    """
    Key queue that stalls consumers while the session is paused.

    Pausing at the queue is what keeps this backend free of core changes: the
    workers block in `get()`, the generator blocks once the queue fills, and
    both resume without either class knowing a pause happened.
    """

    def __init__(self, maxsize: int, resume: asyncio.Event) -> None:
        super().__init__(maxsize=maxsize)
        self._resume = resume

    async def get(self) -> Any:
        await self._resume.wait()
        return await super().get()


class LiveBackend(ScanBackend):
    """Adapter that drives the production scanner and reports to the GUI."""

    name = "live"

    def __init__(self, bus, settings) -> None:
        super().__init__(bus)
        self._settings = settings
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._resume: Optional[asyncio.Event] = None
        self._async_stop: Optional[asyncio.Event] = None
        self.mode = settings.scanner.mode
        self.workers = settings.scanner.workers

        # Throttling bookkeeping (backend thread only)
        self._last_worker_emit: dict[int, float] = {}
        # Last count seen per worker, kept even for the ticks the throttle
        # drops - so the STOPPED events at shutdown report the real totals
        # rather than whatever happened to survive sampling.
        self._worker_checked: dict[int, int] = {}
        self._last_address_emit = 0.0
        self._address_cursor = 0
        # Set once the pipeline is built; PAUSE can be pressed before that.
        self._stats = None

    # ----- ScanBackend hooks -------------------------------------------------
    def request_stop(self) -> None:
        """Called from the render thread - hop onto the asyncio loop to stop."""
        loop, event = self._loop, self._async_stop
        if loop is not None and event is not None and not loop.is_closed():
            loop.call_soon_threadsafe(event.set)

    def pause(self) -> None:
        super().pause()
        self._set_resume(False)

    def resume(self) -> None:
        super().resume()
        self._set_resume(True)

    def _set_resume(self, running: bool) -> None:
        loop, resume = self._loop, self._resume
        if loop is None or resume is None or loop.is_closed():
            return

        def _apply() -> None:
            # Gate and clock flipped together, on the loop thread, so the
            # session clock can never be running while the queue is shut.
            if running:
                resume.set()
                if self._stats is not None:
                    self._stats.resume()
            else:
                resume.clear()
                if self._stats is not None:
                    self._stats.pause()

        loop.call_soon_threadsafe(_apply)

    def _run(self) -> None:
        asyncio.run(self._async_main())

    # ----- scanner wiring ----------------------------------------------------
    async def _async_main(self) -> None:
        from mining_dark.checkers.balance_checker import BalanceChecker, ScanStats
        from mining_dark.core.wallet import FoundWallet, WalletKeys
        from mining_dark.generators.hd_generator import HDWalletGenerator
        from mining_dark.generators.random_generator import RandomKeyGenerator
        from mining_dark.utils import db_lock
        from mining_dark.utils.file_manager import (
            SHUTDOWN,
            FileManager,
            shutdown_persistence,
        )
        from mining_dark.utils.utxo_db import UTXODatabase

        settings = self._settings
        settings.scanner.mode = self.mode
        settings.scanner.workers = self.workers

        self._loop = asyncio.get_running_loop()
        self._resume = asyncio.Event()
        self._resume.set()
        self._async_stop = asyncio.Event()

        # ----- UTXO database -------------------------------------------------
        utxo_db = UTXODatabase(settings.utxo.resolved_db_file())
        utxo_db.open()

        if not utxo_db.is_ready:
            self.bus.emit(DatabaseEvent(status=DBStatus.MISSING))
            self.log(LogLevel.ERROR, t("log.db_missing"))
            utxo_db.close()
            return

        self._emit_database(utxo_db)
        self.log(LogLevel.SUCCESS, t(
            "log.db_loaded",
            count=f"{utxo_db.address_count:,}",
            days=utxo_db.age_days,
        ))
        if utxo_db.needs_update:
            self.log(LogLevel.WARNING, t("log.db_stale", days=utxo_db.age_days))

        # ----- pipeline ------------------------------------------------------
        key_queue: "asyncio.Queue[WalletKeys]" = _PausableQueue(
            settings.scanner.queue_size, self._resume
        )
        found_queue: "asyncio.Queue[FoundWallet]" = asyncio.Queue()
        stats = ScanStats()
        self._stats = stats
        # PAUSE pressed while the database was still opening set the gate but
        # had no clock to stop; adopt that state now rather than start running.
        if self.is_paused:
            stats.pause()

        file_manager = FileManager(
            output_dir=settings.output.resolved_found_wallets_dir(),
            save_csv=settings.output.save_csv,
            json_indent=settings.output.json_indent,
        )

        checker = BalanceChecker(
            settings=settings,
            key_queue=key_queue,
            found_queue=found_queue,
            stats=stats,
            utxo_db=utxo_db,
            on_wallet_found=self._on_wallet_found,
            on_worker_state=self._on_worker_state,
        )

        if settings.scanner.mode == "hd":
            generator = HDWalletGenerator(
                queue=key_queue,
                derivation_paths=settings.hd_wallet.derivation_paths,
                child_count=settings.hd_wallet.child_count,
                stats=stats,
                on_key_generated=self._on_key_generated,
            )
        else:
            generator = RandomKeyGenerator(
                queue=key_queue,
                stats=stats,
                on_key_generated=self._on_key_generated,
            )

        self.log(LogLevel.INFO, t(
            "log.scan_started",
            mode=settings.scanner.mode,
            workers=settings.scanner.workers,
            formats=len(settings.scanner.address_types),
        ))

        def _report_saved(path) -> None:
            self.log(LogLevel.SUCCESS, t("log.wallet_saved", file=path.name))

        def _report_error(exc: BaseException) -> None:
            self.log(LogLevel.ERROR, t("log.wallet_save_failed", error=exc))

        async def persist_found() -> None:
            while True:
                found = await found_queue.get()
                try:
                    if found is SHUTDOWN:
                        return
                    saved = await file_manager.save(found)
                    if saved is not None:
                        _report_saved(saved)
                except Exception as exc:                    # noqa: BLE001
                    _report_error(exc)
                finally:
                    found_queue.task_done()

        # Held for the whole scan: a rebuild starting now would swap the file
        # underneath us, and this connection would keep answering from the
        # deleted one without anything reporting it.
        db_hold = ExitStack()
        try:
            db_hold.enter_context(db_lock.reading(settings.utxo.resolved_db_file()))
        except db_lock.DatabaseBusyError as exc:
            self.log(LogLevel.ERROR, str(exc))
            utxo_db.close()
            return

        persist_task = asyncio.create_task(persist_found(), name="persist")
        tasks = [
            asyncio.create_task(generator.run(), name="generator"),
            asyncio.create_task(checker.run(settings.scanner.workers), name="checker"),
            asyncio.create_task(
                self._telemetry_pump(stats, key_queue, utxo_db), name="telemetry"
            ),
        ]

        try:
            await self._async_stop.wait()
        finally:
            generator.stop()
            checker.stop()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

            try:
                # STOP must not cost a found wallet: drain before letting go.
                await shutdown_persistence(
                    found_queue, persist_task, file_manager,
                    on_saved=_report_saved, on_error=_report_error,
                )
            finally:
                # Released even if the drain raised.  Left held, the flock
                # outlives the scan thread and every later rebuild is refused
                # for a scan that no longer exists.
                utxo_db.close()
                db_hold.close()

            for worker_id in range(settings.scanner.workers):
                self.bus.emit(WorkerEvent(worker_id, WorkerStatus.STOPPED,
                                          self._worker_checked.get(worker_id, 0)))
            # Yellow on screen because the end of a session is worth noticing,
            # INFO on disk because it is how every session ends.
            self.log(LogLevel.WARNING, t(
                "log.scan_stopped",
                keys=f"{stats.keys_generated:,}",
                addresses=f"{stats.addresses_checked:,}",
                found=stats.wallets_found,
            ), file_level=LogLevel.INFO)

    # ----- telemetry ---------------------------------------------------------
    async def _telemetry_pump(self, stats, key_queue: asyncio.Queue,
                              utxo_db) -> None:
        """Sample aggregate counters at a fixed rate and publish them."""
        next_db = time.monotonic() + _DB_PERIOD
        try:
            while True:
                maxsize = key_queue.maxsize or 1
                self.bus.emit(StatsEvent(
                    keys_generated=stats.keys_generated,
                    addresses_checked=stats.addresses_checked,
                    wallets_found=stats.wallets_found,
                    total_found_satoshis=stats.total_found_satoshis,
                    keys_per_second=stats.keys_per_second,
                    checks_per_second=stats.checks_per_second,
                    elapsed_seconds=stats.elapsed_seconds,
                    queue_fill=min(1.0, key_queue.qsize() / maxsize),
                ))

                if time.monotonic() >= next_db:
                    next_db = time.monotonic() + _DB_PERIOD
                    self._emit_database(utxo_db)

                await asyncio.sleep(_STATS_PERIOD)
        except asyncio.CancelledError:
            return

    def _emit_database(self, utxo_db) -> None:
        status = {
            "ok": DBStatus.OK,
            "outdated": DBStatus.OUTDATED,
            "missing": DBStatus.MISSING,
        }.get(utxo_db.status, DBStatus.UNKNOWN)
        last = utxo_db.last_updated
        self.bus.emit(DatabaseEvent(
            status=status,
            address_count=utxo_db.address_count,
            size_mb=utxo_db.db_size_mb,
            last_updated=last.strftime("%d/%m/%Y %H:%M") if last else "-",
            source=utxo_db.source,
            age_days=utxo_db.age_days,
        ))

    # ----- core callbacks ----------------------------------------------------
    def _on_key_generated(self, wallet) -> None:
        """
        Sample one address per tick for the "ultimos enderecos" table.

        The format rotates.  Sampling always started at the head of the list
        and stopped at the first hit, so of the six formats being checked only
        `address_types[0]` ever reached the table - measured at 498 of 498
        samples, all p2pkh, while the counters proved all six were being
        verified.  The table's whole job is to show what the scan is doing.

        Only the public address leaves this method - the private key, WIF and
        mnemonic on `wallet` are never published to the bus.
        """
        now = time.monotonic()
        if now - self._last_address_emit < _ADDRESS_PERIOD:
            return
        self._last_address_emit = now

        types = self._settings.scanner.address_types
        if not types:
            return

        # Start one further along each tick, then take the first format this
        # wallet actually carries - so a type that is configured but absent
        # (an uncompressed address on an HD wallet, say) is skipped rather
        # than costing a turn.
        start = self._address_cursor % len(types)
        self._address_cursor += 1
        for offset in range(len(types)):
            address_type = types[(start + offset) % len(types)]
            address = getattr(wallet, address_type, "")
            if address:
                self.bus.emit(AddressEvent(address, address_type))
                return

    def _on_worker_state(self, worker_id: int, status: str, checked: int) -> None:
        """Throttled per-worker telemetry, fed by `BalanceChecker`'s hook."""
        self._worker_checked[worker_id] = checked

        now = time.monotonic()
        if now - self._last_worker_emit.get(worker_id, 0.0) < _WORKER_PERIOD:
            return
        self._last_worker_emit[worker_id] = now

        try:
            worker_status = WorkerStatus(status)
        except ValueError:
            worker_status = WorkerStatus.SCANNING

        self.bus.emit(WorkerEvent(
            worker_id=worker_id,
            status=worker_status,
            checked=checked,
        ))

    def _on_wallet_found(self, found) -> None:
        self.bus.emit(FoundEvent(
            address=found.primary_address,
            address_type=found.primary_address_type,
            satoshis=found.total_confirmed_satoshis,
        ))
        self.log(LogLevel.SUCCESS, t(
            "log.wallet_found",
            type=found.primary_address_type,
            address=found.primary_address,
            btc=f"{found.total_confirmed_satoshis / 1e8:.8f}",
        ))
