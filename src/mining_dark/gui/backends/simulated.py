"""
Simulated backend - drives the whole dashboard with plausible fake telemetry.

Its only job is to let anyone run `mining-dark gui --simulate` and see every
panel move without a 5 GB UTXO database, a synced Bitcoin Core node, or even
the crypto dependencies.  The numbers are invented; the event shapes are
exactly the ones the live backend emits, so a panel that looks right here looks
right in production.
"""

from __future__ import annotations

import math
import random
import time

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

_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

# How often each kind of event is published, in seconds.  The simulator claims
# thousands of keys per second but only emits a readable trickle of events -
# the same throttling the live backend applies.
_STATS_PERIOD = 0.10
_ADDRESS_PERIOD = 0.06
_LOG_PERIOD = 0.45
_TICK = 0.02

_ADDRESS_TYPES = ("p2pkh", "p2pkh_uncompressed", "p2sh_p2wpkh",
                  "p2wpkh", "p2wsh", "p2tr")

_CHATTER = (
    (LogLevel.INFO, "sim.batch"),
    (LogLevel.INFO, "sim.worker_checked"),
    (LogLevel.DEBUG, "sim.cache"),
    (LogLevel.DEBUG, "sim.queue"),
    (LogLevel.INFO, "sim.checkpoint"),
    (LogLevel.WARNING, "sim.queue_full"),
    (LogLevel.INFO, "sim.reindex"),
)


def _fake_address(address_type: str, rng: random.Random) -> str:
    """Build a syntactically plausible address of the requested type."""
    if address_type in ("p2pkh", "p2pkh_uncompressed"):
        return "1" + "".join(rng.choices(_BASE58, k=33))
    if address_type == "p2sh_p2wpkh":
        return "3" + "".join(rng.choices(_BASE58, k=33))
    if address_type == "p2wpkh":
        return "bc1q" + "".join(rng.choices(_BECH32, k=38))
    if address_type == "p2wsh":
        return "bc1q" + "".join(rng.choices(_BECH32, k=58))
    return "bc1p" + "".join(rng.choices(_BECH32, k=58))


class SimulatedBackend(ScanBackend):
    """Fake scanner that exercises every panel of the dashboard."""

    name = "simulado"

    def __init__(self, bus, *, seed: int | None = None,
                 find_every_seconds: float = 26.0) -> None:
        super().__init__(bus)
        self._rng = random.Random(seed)
        self._find_every = find_every_seconds
        self.mode = "random"
        self.workers = 10

    def _run(self) -> None:
        rng = self._rng
        started = time.monotonic()

        self.bus.emit(DatabaseEvent(
            status=DBStatus.SIMULATED,
            address_count=54_213_907,
            size_mb=4_812.4,
            last_updated=time.strftime("%d/%m/%Y %H:%M"),
            source="simulado",
            age_days=0,
        ))
        self.log(LogLevel.WARNING, t("log.sim_disclaimer"))
        self.log(LogLevel.SUCCESS,
                 t("log.sim_started", mode=self.mode, workers=self.workers))

        # Per-worker simulation state.  `phase` is this simulator's internal
        # batch clock - it decides when a worker "finishes" a batch and never
        # leaves this loop, because a real worker has no such fraction to
        # report and `WorkerEvent` deliberately no longer carries one.
        states = [WorkerStatus.WAITING] * self.workers
        phase = [rng.random() for _ in range(self.workers)]
        checked = [0] * self.workers
        speeds = [rng.uniform(0.55, 1.9) for _ in range(self.workers)]

        keys_generated = 0
        addresses_checked = 0
        wallets_found = 0
        total_satoshis = 0

        next_stats = next_address = next_log = 0.0
        next_find = started + self._find_every * rng.uniform(0.5, 1.0)
        # Time spent parked in wait_while_paused().  Subtracted below for the
        # same reason ScanStats does it: a paused session is not a slow one.
        paused_total = 0.0

        while not self.should_stop:
            before_pause = time.monotonic()
            self.wait_while_paused()
            paused_total += time.monotonic() - before_pause
            if self.should_stop:
                break

            now = time.monotonic()
            elapsed = now - started - paused_total

            # Throughput: ramps up over the first ~8 s, then breathes a little.
            ramp = min(1.0, elapsed / 8.0)
            kps = (2600 + 5200 * ramp) * (1.0 + 0.10 * math.sin(elapsed * 0.7))
            keys_generated += int(kps * _TICK)
            addresses_checked += int(kps * _TICK * len(_ADDRESS_TYPES))

            # ----- worker state machine -------------------------------------
            for i in range(self.workers):
                phase[i] += speeds[i] * _TICK * (0.8 + 0.5 * rng.random())
                if phase[i] >= 1.0:
                    phase[i] = 0.0
                    checked[i] += rng.randint(180, 640)
                    states[i] = (
                        WorkerStatus.VERIFYING if rng.random() < 0.28
                        else WorkerStatus.SCANNING
                    )
                elif states[i] is WorkerStatus.WAITING:
                    states[i] = WorkerStatus.SCANNING

                self.bus.emit(WorkerEvent(i, states[i], checked[i]))

            # ----- recent addresses -----------------------------------------
            if now >= next_address:
                next_address = now + _ADDRESS_PERIOD
                atype = rng.choice(_ADDRESS_TYPES)
                self.bus.emit(AddressEvent(_fake_address(atype, rng), atype))

            # ----- the occasional "hit" -------------------------------------
            if now >= next_find:
                next_find = now + self._find_every * rng.uniform(0.7, 1.6)
                atype = rng.choice(_ADDRESS_TYPES)
                address = _fake_address(atype, rng)
                satoshis = rng.choice([
                    rng.randint(1_000, 90_000),
                    rng.randint(100_000, 5_000_000),
                    rng.randint(5_000_000, 300_000_000),
                ])
                wallets_found += 1
                total_satoshis += satoshis

                hot = rng.randrange(self.workers)
                states[hot] = WorkerStatus.FOUND
                self.bus.emit(WorkerEvent(hot, WorkerStatus.FOUND, checked[hot]))
                self.bus.emit(FoundEvent(address, atype, satoshis))
                self.log(LogLevel.SUCCESS, t(
                    "log.wallet_found", type=atype, address=address,
                    btc=f"{satoshis / 1e8:.8f}",
                ))

            # ----- aggregate stats ------------------------------------------
            if now >= next_stats:
                next_stats = now + _STATS_PERIOD
                self.bus.emit(StatsEvent(
                    keys_generated=keys_generated,
                    addresses_checked=addresses_checked,
                    wallets_found=wallets_found,
                    total_found_satoshis=total_satoshis,
                    # `kps` is the rate this tick was generated at, so it is
                    # already the instantaneous figure the live backend now
                    # reports - no need to average it back down.
                    keys_per_second=kps,
                    checks_per_second=kps * len(_ADDRESS_TYPES),
                    elapsed_seconds=elapsed,
                    queue_fill=0.45 + 0.45 * abs(math.sin(elapsed * 0.35)),
                ))

            # ----- background chatter ---------------------------------------
            if now >= next_log:
                next_log = now + _LOG_PERIOD * rng.uniform(0.6, 1.8)
                level, key = rng.choice(_CHATTER)
                self.log(level, t(
                    key,
                    n=rng.randint(120, 9800),
                    w=rng.randrange(self.workers),
                    ms=rng.randint(3, 180),
                    pct=rng.randint(41, 99),
                ))

            time.sleep(_TICK)

        # Park every worker so the UI does not freeze mid-animation.
        for i in range(self.workers):
            self.bus.emit(WorkerEvent(i, WorkerStatus.STOPPED, checked[i]))
        self.log(LogLevel.WARNING, t("log.sim_stopped"), file_level=LogLevel.INFO)
