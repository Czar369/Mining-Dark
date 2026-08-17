"""
Thread-safe plumbing between a scan backend and the Dear PyGui front-end.

The GUI runs Dear PyGui on the main thread and never lets a backend thread
touch a DPG item.  Instead every backend publishes immutable events onto an
`EventBus` (a bounded `queue.Queue`); the render loop drains the bus once per
frame and folds the events into a `UIState`, which the panels then read.

Nothing in this module imports Dear PyGui - it stays testable headless.
"""

from __future__ import annotations

import queue
import time
from collections import deque
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Optional, Union


# ═══════════════════════════════════════════════════════════════════════════════
#  Enumerations
# ═══════════════════════════════════════════════════════════════════════════════
class LogLevel(str, Enum):
    """Severity of a STREAM LOG line - drives its colour in the log panel."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARN"
    ERROR = "ERROR"


class WorkerStatus(str, Enum):
    """
    Lifecycle state of a single scan worker, as shown in the left panel.

    `WAITING` is not "this worker has nothing to do" - it is "this worker asked
    the queue for a key and none arrived".  It was called IDLE, which read as
    the worker sitting one out by choice and collided with the session-level
    state in the header badge; what it actually reports is starvation, the
    generator failing to keep the queue fed.
    """

    WAITING = "WAITING"
    SCANNING = "SCANNING"
    VERIFYING = "VERIFYING"
    FOUND = "FOUND"
    STOPPED = "STOPPED"


class RunState(str, Enum):
    """Overall state of the scan session."""

    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    ERROR = "ERROR"


class DBStatus(str, Enum):
    """Health of the local UTXO database."""

    UNKNOWN = "UNKNOWN"
    MISSING = "MISSING"
    OUTDATED = "OUTDATED"
    OK = "OK"
    SIMULATED = "SIMULATED"


# ═══════════════════════════════════════════════════════════════════════════════
#  Events - immutable messages produced by a backend thread
# ═══════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True, slots=True)
class LogEvent:
    """
    A single line for the STREAM LOG panel.

    `level` is what the *screen* does with the line - it picks the colour.
    `file_level` is what the *log file* makes of it, and only differs when the
    two disagree: a scan being stopped is styled WARNING so it stands out in
    the panel, but it is routine, and mirroring that severity into loguru put a
    normal shutdown into `errors_*.log` alongside real faults.  Left None, the
    file follows the screen.
    """

    level: LogLevel
    message: str
    ts: float = field(default_factory=time.time)
    file_level: Optional[LogLevel] = None


@dataclass(frozen=True, slots=True)
class StatsEvent:
    """Aggregate counters - mirrors `checkers.balance_checker.ScanStats`."""

    keys_generated: int = 0
    addresses_checked: int = 0
    wallets_found: int = 0
    total_found_satoshis: int = 0
    keys_per_second: float = 0.0
    checks_per_second: float = 0.0
    elapsed_seconds: float = 0.0
    # 0.0-1.0 fill ratio of the key queue - feeds the SCAN phase bar
    queue_fill: float = 0.0


@dataclass(frozen=True, slots=True)
class WorkerEvent:
    """
    State change of one worker slot.

    Carries no `progress`: a worker checks a wallet in microseconds and has no
    long-running unit of work to be a fraction of, so the live backend had to
    invent one - `(checked % 64) / 64`, a sawtooth that meant nothing.  What a
    worker genuinely knows is its status and how many wallets it has checked,
    and the panel derives its bar from the latter (`UIState.worker_shares`).
    """

    worker_id: int
    status: WorkerStatus
    checked: int = 0


@dataclass(frozen=True, slots=True)
class AddressEvent:
    """
    An address that was just verified.

    Carries the public address only - private keys, WIFs and mnemonics never
    cross this boundary, so they can never reach the screen.
    """

    address: str
    address_type: str
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class FoundEvent:
    """A wallet that turned out to hold a balance."""

    address: str
    address_type: str
    satoshis: int
    ts: float = field(default_factory=time.time)

    @property
    def btc(self) -> float:
        return self.satoshis / 1e8


@dataclass(frozen=True, slots=True)
class RunStateEvent:
    """The backend announcing a transition (started, paused, crashed, ...)."""

    state: RunState
    detail: str = ""


@dataclass(frozen=True, slots=True)
class DatabaseEvent:
    """Snapshot of the local UTXO database health, for the footer strip."""

    status: DBStatus = DBStatus.UNKNOWN
    address_count: int = 0
    size_mb: float = 0.0
    last_updated: str = "-"
    source: str = "-"
    age_days: int = 0


@dataclass(frozen=True, slots=True)
class NodeEvent:
    """Snapshot of the Bitcoin Core node, for the Node & UTXO settings tab."""

    available: bool = True        # bitcoind / bitcoin-cli found on PATH
    running: bool = False
    reachable: bool = False       # RPC answered
    chain: str = "-"
    blocks: int = 0
    headers: int = 0
    progress: float = 0.0         # 0.0-1.0 verification progress
    #: Header download, the phase that runs before any block arrives.  Kept
    #: apart from `progress` because the two never move at the same time: the
    #: headers finish first, and only then does verification leave zero.
    header_progress: float = 0.0
    header_height: int = 0
    size_bytes: int = 0
    pruned: bool = False
    ibd: bool = False             # initial block download in progress
    snapshot_active: bool = False
    #: Median height the peers report, 0 when none answered.  The node's own
    #: `blocks`/`headers` cannot reveal a blocked chain - Core reports
    #: `headers` as the best *valid* header chain, so both read equal and the
    #: node looks caught up while the network moves on without it.
    peer_height: int = 0
    #: Height of the lowest branch above the tip that Core marked invalid, 0
    #: when the chain is clean.  Terminal: it survives restarts and the block
    #: is refused from every peer until someone clears it.
    invalid_height: int = 0
    detail: str = ""
    checked_at: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class DiskEvent:
    """Free space where the UTXO database lives, and what a rebuild will want."""

    path: str = "-"
    free_bytes: int = 0
    total_bytes: int = 0
    #: Rough peak requirement: the old database, the new one being built, and
    #: the CSV export all exist at the same time partway through a rebuild.
    estimated_rebuild_bytes: int = 0
    #: The two halves of that estimate, used to turn the growing temp files of
    #: a rebuild in flight into a progress fraction.  Order of magnitude only.
    estimated_csv_bytes: int = 0
    estimated_db_bytes: int = 0

    @property
    def sufficient(self) -> bool:
        return self.estimated_rebuild_bytes <= 0 or \
            self.free_bytes >= self.estimated_rebuild_bytes


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """A long background operation (node start, UTXO rebuild) starting or ending."""

    name: str
    running: bool
    detail: str = ""


Event = Union[
    LogEvent,
    StatsEvent,
    WorkerEvent,
    AddressEvent,
    FoundEvent,
    RunStateEvent,
    DatabaseEvent,
    NodeEvent,
    DiskEvent,
    TaskEvent,
]


# ═══════════════════════════════════════════════════════════════════════════════
#  Event bus
# ═══════════════════════════════════════════════════════════════════════════════
class EventBus:
    """
    Bounded, lossy, thread-safe queue.

    A scanner can emit tens of thousands of events per second while the GUI
    only redraws ~60 times per second, so the bus must never block the producer.
    When it is full the oldest event is dropped - stale telemetry is worthless
    and a stalled scanner is not.
    """

    __slots__ = ("_dropped", "_queue")

    def __init__(self, maxsize: int = 8192) -> None:
        self._queue: "queue.Queue[Event]" = queue.Queue(maxsize=maxsize)
        self._dropped = 0

    def emit(self, event: Event) -> None:
        """Publish an event.  Safe to call from any thread; never blocks."""
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            try:
                self._queue.get_nowait()      # drop oldest
                self._queue.put_nowait(event)
                self._dropped += 1
            except (queue.Empty, queue.Full):  # pragma: no cover - race, harmless
                self._dropped += 1

    def drain(self, max_items: int = 2048) -> list[Event]:
        """Pop up to `max_items` events.  Call from the render thread only."""
        out: list[Event] = []
        for _ in range(max_items):
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    @property
    def dropped(self) -> int:
        return self._dropped


# ═══════════════════════════════════════════════════════════════════════════════
#  UI state - written by the render thread, read by the panels
# ═══════════════════════════════════════════════════════════════════════════════
@dataclass(slots=True)
class WorkerRow:
    """One row in the left-hand worker list."""

    worker_id: int
    status: WorkerStatus = WorkerStatus.WAITING
    checked: int = 0
    # Wall-clock of the last update - used to fade quiet nodes in the graph
    updated_at: float = field(default_factory=time.monotonic)


@dataclass(slots=True)
class LogLine:
    level: LogLevel
    message: str
    ts: float


#: Fields a probe only knows when the RPC answered.  A probe that could not
#: reach it carries zeros for all of them, and zeros are not "the node has no
#: blocks" - they are "nobody asked successfully".
_RPC_ONLY_FIELDS = ("chain", "blocks", "headers", "progress", "header_progress",
                    "header_height", "size_bytes", "pruned", "ibd",
                    "snapshot_active", "peer_height", "invalid_height")


def _merge_node(previous: NodeEvent, fresh: NodeEvent) -> NodeEvent:
    """
    Fold a new node probe into what was already known.

    A probe taken while bitcoind is busy comes back `running=True,
    reachable=False` with every numeric field at zero - roughly one probe in
    twenty against a syncing node, measured.  Taking it at face value made the
    whole node section drop to zero and come back a moment later: both progress
    bars snapping to 0%, the block counts blanking, and every gated button
    flipping to disabled and back, about once a minute.

    Those zeros say nothing happened, not that nothing is there.  So an
    unreachable probe keeps the last figures that were actually observed and
    updates only what it genuinely learned - that the process is up and the RPC
    is not answering right now.  A node that is *down* reports `running=False`
    from a pid check that never lies, and resets the readouts as it should.
    """
    if not (fresh.running and not fresh.reachable):
        return fresh
    if not previous.reachable:
        # Nothing better on record - a node still warming up has never answered.
        return fresh
    return replace(fresh, **{f: getattr(previous, f) for f in _RPC_ONLY_FIELDS})


class UIState:
    """
    Everything the panels need to draw a frame.

    Mutated exclusively by `apply()` on the render thread, so no locking is
    needed anywhere in the panel code.
    """

    def __init__(self, worker_count: int = 10, max_log_lines: int = 400,
                 max_recent: int = 14) -> None:
        self.run_state: RunState = RunState.STOPPED
        self.run_detail: str = ""

        self.stats: StatsEvent = StatsEvent()
        self.database: DatabaseEvent = DatabaseEvent()
        self.node: NodeEvent = NodeEvent()
        self.disk: DiskEvent = DiskEvent()

        # Name of the long background operation in flight, "" when idle.
        self.task: str = ""

        self.workers: list[WorkerRow] = []
        self.set_worker_count(worker_count)

        self.max_log_lines = max_log_lines
        self.logs: "deque[LogLine]" = deque(maxlen=max_log_lines)
        # Lines that arrived since the last time the log panel rendered
        self.pending_logs: list[LogLine] = []

        self.recent: "deque[AddressEvent]" = deque(maxlen=max_recent)
        self.found: list[FoundEvent] = []

        # Set to True by apply() so panels can skip untouched work
        self.dirty_workers: bool = True
        self.dirty_recent: bool = True
        self.dirty_found: bool = True


    # ----- worker slots ------------------------------------------------------
    def set_worker_count(self, count: int) -> None:
        """Resize the worker list, preserving existing rows where possible."""
        count = max(1, count)
        current = len(self.workers)
        if count > current:
            self.workers.extend(
                WorkerRow(worker_id=i) for i in range(current, count)
            )
        elif count < current:
            del self.workers[count:]
        self.dirty_workers = True

    def reset_counters(self) -> None:
        """Clear per-session data before a new scan starts."""
        self.stats = StatsEvent()
        self.recent.clear()
        self.found.clear()
        for row in self.workers:
            row.status = WorkerStatus.WAITING
            row.checked = 0
        self.dirty_workers = self.dirty_recent = self.dirty_found = True

    def clear_logs(self) -> None:
        self.logs.clear()
        self.pending_logs.clear()

    # ----- event folding -----------------------------------------------------
    def apply(self, events: list[Event]) -> None:
        """Fold a drained batch of events into the state."""
        for ev in events:
            if isinstance(ev, StatsEvent):
                self.stats = ev
            elif isinstance(ev, WorkerEvent):
                self._apply_worker(ev)
            elif isinstance(ev, LogEvent):
                line = LogLine(ev.level, ev.message, ev.ts)
                self.logs.append(line)
                self.pending_logs.append(line)
            elif isinstance(ev, AddressEvent):
                self.recent.appendleft(ev)
                self.dirty_recent = True
            elif isinstance(ev, FoundEvent):
                self.found.append(ev)
                self.dirty_found = True
            elif isinstance(ev, RunStateEvent):
                self.run_state = ev.state
                self.run_detail = ev.detail
            elif isinstance(ev, DatabaseEvent):
                self.database = ev
            elif isinstance(ev, NodeEvent):
                self.node = _merge_node(self.node, ev)
            elif isinstance(ev, DiskEvent):
                self.disk = ev
            elif isinstance(ev, TaskEvent):
                self.task = ev.name if ev.running else ""

        # Keep pending_logs bounded even if the panel never consumes it
        if len(self.pending_logs) > self.max_log_lines:
            del self.pending_logs[: -self.max_log_lines]

    def _apply_worker(self, ev: WorkerEvent) -> None:
        if not 0 <= ev.worker_id < len(self.workers):
            return
        row = self.workers[ev.worker_id]
        row.status = ev.status
        row.checked = ev.checked
        row.updated_at = time.monotonic()
        self.dirty_workers = True

    # ----- derived values ----------------------------------------------------
    @property
    def is_active(self) -> bool:
        return self.run_state in (RunState.RUNNING, RunState.PAUSED, RunState.STARTING)

    @property
    def total_btc_found(self) -> float:
        return self.stats.total_found_satoshis / 1e8

    def session_hms(self) -> str:
        """
        How long the scan has actually been running, HH:MM:SS.

        Scan time, not window time: it reads 00:00:00 until START is pressed,
        and that is the point - the number exists to say how long the counters
        beside it took to get where they are.  Pauses do not count, because
        `ScanStats.elapsed_seconds` stops with the pipeline.
        """
        return hms(self.stats.elapsed_seconds)

    def worker_shares(self) -> list:
        """
        Each worker's checked count as a fraction of the busiest one's.

        Healthy means every bar full - all the workers are keeping up with each
        other.  One that stalls or slows falls behind the leader and its bar
        visibly shrinks while the rest stay full, which is the only per-worker
        fact worth watching.

        The bars used to show `(checked % 64) / 64`, a sawtooth: with twenty
        workers draining one queue at the same rate, all twenty showed the same
        number (measured at 16%, 17%, 19% across the whole list) and none of it
        meant anything.

        Against the maximum rather than the mean, so nothing needs clamping:
        the leader is 1.0 by construction and everyone else sits below it.
        """
        best = max((row.checked for row in self.workers), default=0)
        if best <= 0:
            # Nothing has been checked yet - before the first key, or right
            # after reset_counters().  Zero is honest; 1.0 would claim every
            # worker is keeping up before any of them has done anything.
            return [0.0 for _ in self.workers]
        return [row.checked / best for row in self.workers]



def hms(seconds: float) -> str:
    """Seconds as HH:MM:SS.  Hours are not wrapped - a long run says 27:14:03."""
    secs = max(0, int(seconds))
    return f"{secs // 3600:02d}:{(secs % 3600) // 60:02d}:{secs % 60:02d}"


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if value < lo else hi if value > hi else value


def abbreviate(address: str, head: int = 10, tail: int = 6) -> str:
    """Shorten an address for narrow columns: 'bc1qxy...k3f9pz'."""
    if len(address) <= head + tail + 3:
        return address
    return f"{address[:head]}...{address[-tail:]}"


def fit_address(address: str, budget: int) -> str:
    """
    An address trimmed to `budget` characters, keeping both ends.

    One rule for every format.  Anything that fits is returned untouched, so at
    the panel's normal width the 34- and 42-character formats print whole and
    only the 62-character bech32 ones lose a middle section.  What is dropped
    comes out of the middle because both ends carry the meaning: the prefix
    says which format it is, and the tail is what anyone eyeballing a match
    compares first.

    Trimming rather than letting it overflow is the point - Dear PyGui clips a
    too-wide cell with no ellipsis, which leaves a cut address looking exactly
    like a whole one.
    """
    if budget <= 0 or len(address) <= budget:
        return address
    if budget <= len(_ELLIPSIS):
        return address[:budget]

    keep = budget - len(_ELLIPSIS)
    head = keep - keep // 2          # odd remainders go to the prefix
    return f"{address[:head]}{_ELLIPSIS}{address[len(address) - keep // 2:]}"


_ELLIPSIS = "..."


def guess_address_type(address: str) -> str:
    """Best-effort label for an address, used by the simulated backend."""
    if address.startswith("1"):
        return "p2pkh"
    if address.startswith("3"):
        return "p2sh_p2wpkh"
    if address.startswith("bc1p"):
        return "p2tr"
    if address.startswith("bc1q"):
        return "p2wpkh" if len(address) == 42 else "p2wsh"
    return "unknown"


def optional_int(value: Optional[int], default: int = 0) -> int:
    return default if value is None else value
