"""Backend lifecycle and the guarantee that no secret ever reaches the GUI."""

from __future__ import annotations

import time

from mining_dark.gui.backends.base import ScanBackend
from mining_dark.gui.backends.simulated import SimulatedBackend, _fake_address
from mining_dark.gui.state import (
    AddressEvent,
    EventBus,
    FoundEvent,
    LogEvent,
    RunState,
    RunStateEvent,
    StatsEvent,
    UIState,
    WorkerEvent,
    guess_address_type,
)
from mining_dark.utils.logger import contains_secret


class _CountingBackend(ScanBackend):
    """Minimal backend that just spins until asked to stop."""

    name = "counting"

    def __init__(self, bus: EventBus) -> None:
        super().__init__(bus)
        self.ticks = 0

    def _run(self) -> None:
        while not self.should_stop:
            self.wait_while_paused()
            if self.should_stop:
                break
            self.ticks += 1
            time.sleep(0.005)


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ----- lifecycle -------------------------------------------------------------
def test_start_stop_lifecycle() -> None:
    bus = EventBus()
    backend = _CountingBackend(bus)

    assert not backend.is_running
    backend.start(workers=3)
    assert _wait_for(lambda: backend.ticks > 0)
    assert backend.is_running

    backend.stop()
    assert not backend.is_running

    states = [e.state for e in bus.drain() if isinstance(e, RunStateEvent)]
    assert RunState.RUNNING in states
    assert states[-1] is RunState.STOPPED


def test_start_is_idempotent_while_running() -> None:
    bus = EventBus()
    backend = _CountingBackend(bus)
    backend.start()
    thread = backend._thread

    backend.start()                      # must not spawn a second thread
    assert backend._thread is thread

    backend.stop()


def test_pause_halts_progress_and_resume_continues() -> None:
    bus = EventBus()
    backend = _CountingBackend(bus)
    backend.start()
    assert _wait_for(lambda: backend.ticks > 0)

    backend.pause()
    assert backend.is_paused
    time.sleep(0.08)
    frozen = backend.ticks
    time.sleep(0.08)
    assert backend.ticks == frozen       # no work happens while paused

    backend.resume()
    assert _wait_for(lambda: backend.ticks > frozen)

    backend.stop()


def test_stop_while_paused_still_exits() -> None:
    bus = EventBus()
    backend = _CountingBackend(bus)
    backend.start()
    backend.pause()

    backend.stop(timeout=2.0)
    assert not backend.is_running


def test_backend_failure_is_reported_not_raised() -> None:
    class _Exploding(ScanBackend):
        name = "boom"

        def _run(self) -> None:
            raise RuntimeError("disco caiu")

    bus = EventBus()
    backend = _Exploding(bus)
    backend.start()
    backend.stop(timeout=2.0)

    events = bus.drain()
    errors = [e for e in events if isinstance(e, RunStateEvent) and e.state is RunState.ERROR]
    assert errors and "disco caiu" in errors[0].detail


# ----- simulated backend -----------------------------------------------------
def test_simulated_backend_feeds_every_panel() -> None:
    bus = EventBus()
    backend = SimulatedBackend(bus, seed=1, find_every_seconds=0.2)
    collected: list = []

    def saw_a_hit() -> bool:
        collected.extend(bus.drain())
        return any(isinstance(e, FoundEvent) for e in collected)

    backend.start(mode="random", workers=6)
    assert _wait_for(saw_a_hit, 5.0)
    # FoundEvent fires immediately; the aggregate counter lands on the next
    # StatsEvent tick, so give the telemetry sampler a beat to catch up.
    time.sleep(0.3)
    backend.stop()
    collected.extend(bus.drain())

    state = UIState(worker_count=6)
    state.apply(collected)

    kinds = {type(e) for e in collected}
    assert {StatsEvent, WorkerEvent, AddressEvent, FoundEvent, LogEvent} <= kinds
    assert state.stats.keys_generated > 0
    assert state.stats.wallets_found >= 1
    assert len(state.recent) > 0


def test_simulated_addresses_have_the_right_shape() -> None:
    import random

    rng = random.Random(3)
    for address_type in ("p2pkh", "p2sh_p2wpkh", "p2wpkh", "p2wsh", "p2tr"):
        address = _fake_address(address_type, rng)
        assert guess_address_type(address) == address_type


# ----- the security guarantee ------------------------------------------------
def test_no_backend_event_ever_carries_key_material() -> None:
    """
    The bus is the only path from the scanner to the screen.  Nothing crossing
    it may look like a private key, a WIF, or a mnemonic.
    """
    bus = EventBus()
    backend = SimulatedBackend(bus, seed=11, find_every_seconds=0.15)
    backend.start(workers=4)
    time.sleep(1.0)
    backend.stop()

    events = bus.drain(max_items=100_000)
    assert events

    for event in events:
        for value in (getattr(event, "message", ""), getattr(event, "address", "")):
            assert not contains_secret(value), f"secret leaked via {event!r}"
