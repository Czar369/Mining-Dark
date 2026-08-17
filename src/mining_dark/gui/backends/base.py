"""
Backend contract for the GUI.

The dashboard does not care whether the numbers on screen come from a real
scan or from a simulator - it only talks to a `ScanBackend`.  That indirection
is what lets `mining-dark gui --simulate` exercise the whole interface with no
UTXO database present.

Backends run on their own thread and publish to an `EventBus`.  They must never
call into Dear PyGui.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod

from mining_dark.i18n import t
from mining_dark.gui.state import (
    EventBus,
    LogEvent,
    LogLevel,
    RunState,
    RunStateEvent,
)


class ScanBackend(ABC):
    """
    A source of scan telemetry.

    Lifecycle: `start()` -> (`pause()` / `resume()`)* -> `stop()`.  Every method
    is called from the render thread and must return promptly - do the work on
    the backend thread.
    """

    #: Shown in the header badge.
    name: str = "backend"

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()   # set == paused

    # ----- public API --------------------------------------------------------
    def start(self, *, mode: str = "random", workers: int = 10) -> None:
        """Spin up the backend thread.  No-op if already running."""
        if self.is_running:
            return

        self._stop_event.clear()
        self._pause_event.clear()
        self.mode = mode
        self.workers = workers

        self.bus.emit(RunStateEvent(RunState.STARTING))
        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"mining-dark-{self.name}",
            daemon=True,
        )
        self._thread.start()

    def pause(self) -> None:
        if self.is_running and not self._pause_event.is_set():
            self._pause_event.set()
            self.bus.emit(RunStateEvent(RunState.PAUSED))
            self.log(LogLevel.WARNING, t("log.paused"), file_level=LogLevel.INFO)

    def resume(self) -> None:
        if self.is_running and self._pause_event.is_set():
            self._pause_event.clear()
            self.bus.emit(RunStateEvent(RunState.RUNNING))
            self.log(LogLevel.INFO, t("log.resumed"))

    def toggle_pause(self) -> None:
        self.resume() if self.is_paused else self.pause()

    def stop(self, timeout: float = 6.0) -> None:
        """
        Ask the backend to wind down.

        `timeout=0` returns immediately - the UI uses that so the STOP button
        never freezes a frame; `is_running` keeps reporting True until the
        thread actually exits, which is what stops a second session from
        starting on top of a dying one.
        """
        thread = self._thread
        if thread is None or not thread.is_alive():
            self._thread = None
            return

        self.bus.emit(RunStateEvent(RunState.STOPPING))
        self._stop_event.set()
        self._pause_event.clear()          # a paused loop must be able to exit
        self.request_stop()

        if timeout > 0 and thread is not threading.current_thread():
            thread.join(timeout=timeout)
            if not thread.is_alive():
                self._thread = None

    # ----- state -------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self) -> bool:
        return self._pause_event.is_set()

    @property
    def should_stop(self) -> bool:
        return self._stop_event.is_set()

    def wait_while_paused(self, poll: float = 0.05) -> None:
        """Block the backend thread while paused, returning early on stop."""
        while self._pause_event.is_set() and not self._stop_event.is_set():
            self._stop_event.wait(poll)

    # ----- helpers for subclasses -------------------------------------------
    def log(self, level: LogLevel, message: str,
            file_level: "LogLevel | None" = None) -> None:
        """`file_level` overrides the severity written to the log file - see `LogEvent`."""
        self.bus.emit(LogEvent(level, message, file_level=file_level))

    def request_stop(self) -> None:  # noqa: B027 - optional hook, not abstract
        """
        Hook for backends that need to nudge something other than the stop flag
        (an asyncio loop, a subprocess).  Called from the render thread.

        Deliberately concrete and empty: most backends only need `should_stop`.
        """

    # ----- thread body -------------------------------------------------------
    def _thread_main(self) -> None:
        try:
            self.bus.emit(RunStateEvent(RunState.RUNNING))
            self._run()
        except Exception as exc:                      # noqa: BLE001 - surface it
            self.bus.emit(RunStateEvent(RunState.ERROR, str(exc)))
            self.log(LogLevel.ERROR, t("log.backend_failed", error=exc))
        finally:
            self.bus.emit(RunStateEvent(RunState.STOPPED))

    @abstractmethod
    def _run(self) -> None:
        """Backend body.  Runs on the backend thread until `should_stop`."""
