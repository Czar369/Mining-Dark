"""
Side operations the dashboard triggers but does not own: Bitcoin Core control,
UTXO database rebuilds, and handing a file to the desktop.

Everything here is slow, blocking, or both, so it runs on a background thread
and reports back through the `EventBus` - the same one-way channel the scan
backends use.  Nothing in this module touches Dear PyGui.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Optional

from mining_dark.i18n import t
from mining_dark.gui.state import (
    DiskEvent,
    EventBus,
    LogLevel,
    NodeEvent,
    TaskEvent,
)


class TaskRunner:
    """
    Runs one long operation at a time.

    Serialising is deliberate: starting a UTXO rebuild while bitcoind is being
    stopped would corrupt the chainstate, and a queue of node commands is never
    what the person clicking actually wanted.
    """

    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self._lock = threading.Lock()
        self._current: Optional[str] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._current is not None

    @property
    def current(self) -> str:
        with self._lock:
            return self._current or ""

    def submit(self, name: str, fn: Callable[[], None]) -> bool:
        """Start `fn` on a background thread.  Returns False if one is running."""
        with self._lock:
            if self._current is not None:
                return False
            self._current = name

        self.bus.emit(TaskEvent(name, running=True))
        self._thread = threading.Thread(
            target=self._run, args=(name, fn), name=f"mining-dark-{name}", daemon=True
        )
        self._thread.start()
        return True

    def _run(self, name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except SystemExit:
            # The export pipeline is shared with the CLI, where refusing means
            # exiting.  SystemExit gets its own message here; the invariant it
            # first exposed - every exit path must report completion - is what
            # the catch-all below now guarantees for good.
            self.bus.emit(TaskEvent(name, running=False, detail=t("log.task_refused")))
            self.bus.emit(_log(LogLevel.ERROR, "log.task_refused"))
        except BaseException as exc:                    # noqa: BLE001
            # BaseException, not Exception: a job that dies on anything other
            # than the SystemExit caught above would otherwise skip this emit,
            # the thread would end, and the panel - driven by these events -
            # would keep showing a task that no longer exists, every button
            # disabled behind it until the window is restarted.  A background
            # worker must always report that it stopped.
            self.bus.emit(TaskEvent(name, running=False, detail=str(exc)))
            self.bus.emit(_log(LogLevel.ERROR, "log.node_failed", error=str(exc)))
        else:
            self.bus.emit(TaskEvent(name, running=False))
        finally:
            with self._lock:
                self._current = None

    def join(self, timeout: float = 2.0) -> None:
        """Wait briefly for the in-flight task - used on shutdown."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)


def _log(level: LogLevel, key: str, file_level: "LogLevel | None" = None,
         **kwargs: object):
    from mining_dark.gui.state import LogEvent
    return LogEvent(level, t(key, **kwargs), file_level=file_level)


# ═══════════════════════════════════════════════════════════════════════════════
#  Bitcoin Core
# ═══════════════════════════════════════════════════════════════════════════════
def probe_node() -> NodeEvent:
    """
    Collect the node's state.  Blocking - the RPC call can take seconds, so
    only ever call this from a `TaskRunner` job.
    """
    from mining_dark import bitcoin_node, paths

    missing = bitcoin_node.require_binaries("bitcoind", "bitcoin-cli")
    if missing:
        return NodeEvent(available=False, detail=t("settings.node.binaries_missing"))

    if not bitcoin_node.is_running():
        # Carry the reason it is down.  "stopped" with every field dashed looks
        # identical whether the user never started it or it died on startup
        # eight seconds ago - and in the second case pressing START again just
        # repeats the crash, with the explanation buried in debug.log.
        return NodeEvent(
            available=True,
            running=False,
            detail=bitcoin_node.last_startup_error(),
        )

    info = bitcoin_node.getblockchaininfo()
    if info is None:
        # Process is up but RPC did not answer: still warming up, or the
        # credentials file was regenerated behind its back.
        return NodeEvent(available=True, running=True, reachable=False)

    snapshot = bitcoin_node.snapshot_status()
    # Asked of the peers, not of the node: a blocked chain reports blocks ==
    # headers and looks healthy from the inside.  See `NodeEvent.peer_height`.
    branch = bitcoin_node.invalid_branch()
    # Read from the log: the RPC reports blocks and headers as 0 for the whole
    # pre-synchronisation pass, which is minutes of the node working with
    # nothing to show for it on screen.
    header = bitcoin_node.header_sync_progress()

    return NodeEvent(
        available=True,
        running=True,
        reachable=True,
        chain=str(info.get("chain", "-")),
        blocks=int(info.get("blocks", 0)),
        headers=int(info.get("headers", 0)),
        progress=float(info.get("verificationprogress", 0.0)),
        header_progress=header[0] if header else 0.0,
        header_height=header[1] if header else 0,
        size_bytes=int(info.get("size_on_disk", 0)),
        pruned=bool(info.get("pruned", False)),
        ibd=bool(info.get("initialblockdownload", False)),
        snapshot_active=bool(snapshot and snapshot.get("active")),
        peer_height=bitcoin_node.peer_block_height(),
        invalid_height=branch[0] if branch else 0,
        detail=str(paths.BITCOIN_CORE_DIR),
    )


def start_node(bus: EventBus, *, reindex: bool = False) -> None:
    """
    Start bitcoind against the project datadir and report what actually happened.

    `bitcoin_node.start()` only proves the daemon forked.  A node that quits
    during startup - a chainstate corrupted by an unclean shutdown is the
    common one - forks just as successfully, so reporting success there left
    the panel claiming a running node that had already exited, with the reason
    sitting unread in debug.log.
    """
    from mining_dark import bitcoin_node, paths

    bus.emit(_log(LogLevel.INFO, "log.node_starting"))
    bitcoin_node.start(reindex=reindex)

    if bitcoin_node.wait_until_ready(timeout=_START_WAIT_TIMEOUT):
        bus.emit(_log(LogLevel.SUCCESS, "log.node_started", path=str(paths.BITCOIN_CORE_DIR)))
    elif bitcoin_node.is_running():
        # Loading a large block index legitimately outlasts the wait.
        bus.emit(_log(LogLevel.WARNING, "log.node_slow_start"))
    elif bitcoin_node.pruned_verify_failure():
        # Not corruption: startup verification wanted undo data that pruning
        # had deleted.  Core's own advice here is `-reindex`, which on a pruned
        # node discards a working download to rebuild from blocks that no
        # longer exist - days of work, to fix nothing.  Retry shallower once
        # and say so, rather than leave the button failing and the log quoting
        # the advice that would destroy the datadir.
        bus.emit(_log(LogLevel.WARNING, "log.node_prune_verify"))
        bitcoin_node.start(reindex=reindex,
                           check_level=bitcoin_node.SHALLOW_CHECK_LEVEL)
        if bitcoin_node.wait_until_ready(timeout=_START_WAIT_TIMEOUT):
            bus.emit(_log(LogLevel.SUCCESS, "log.node_started",
                          path=str(paths.BITCOIN_CORE_DIR)))
        elif bitcoin_node.is_running():
            bus.emit(_log(LogLevel.WARNING, "log.node_slow_start"))
        else:
            bus.emit(_log(LogLevel.ERROR, "log.node_start_failed",
                          error=bitcoin_node.last_startup_error()
                          or t("log.node_start_no_reason")))
    else:
        bus.emit(_log(
            LogLevel.ERROR,
            "log.node_start_failed",
            error=bitcoin_node.last_startup_error() or t("log.node_start_no_reason"),
        ))

    bus.emit(probe_node())


def stop_node(bus: EventBus, *, timeout: float = 90.0) -> None:
    """Ask bitcoind to shut down cleanly, then re-probe."""
    from mining_dark import bitcoin_node

    bus.emit(_log(LogLevel.INFO, "log.node_stopping"))
    exited = bitcoin_node.stop(timeout=timeout)
    if exited:
        bus.emit(_log(LogLevel.SUCCESS, "log.node_stopped"))
    else:
        bus.emit(_log(LogLevel.WARNING, "log.node_stop_timeout"))
    bus.emit(probe_node())


def refresh_node(bus: EventBus, settings=None) -> None:
    bus.emit(probe_node())
    if settings is not None:
        bus.emit(probe_disk(settings))


# ═══════════════════════════════════════════════════════════════════════════════
#  Storage headroom
# ═══════════════════════════════════════════════════════════════════════════════
#: Fallbacks used only until a rebuild has been measured on this machine - the
#: first one ever, or a database that predates the recording.  Both are ratios
#: against the chainstate the export reads, taken on mainnet at height 961,897
#: (12.66 GB chainstate -> 3.12 GB database).  Order of magnitude, not promises;
#: `_last_rebuild_sizes` replaces them with the real thing as soon as there is
#: one to use.
_CSV_TO_CHAINSTATE = 0.7
_DB_TO_CHAINSTATE = 0.25


def _last_rebuild_sizes(db_file) -> tuple:
    """
    (csv_bytes, db_bytes) from the last rebuild, or (0, 0) if unrecorded.

    Written by `utxo_updater` when an import finishes.  Reading it is a tiny
    SQLite query against a file the panel already stats every few seconds, and
    any failure is answered with zeros - this only sharpens an estimate, so it
    must never be able to break the panel that shows it.
    """
    from mining_dark.utils.utxo_db import UTXODatabase

    try:
        with UTXODatabase(db_file) as db:
            return (int(db.get_meta("last_csv_bytes", "0") or 0),
                    int(db.get_meta("last_db_bytes", "0") or 0))
    except Exception:                          # noqa: BLE001 - an estimate only
        return 0, 0

#: How long `start_node` waits for the RPC before handing the TaskRunner back.
#: The runner serialises everything, so this is also how long STOP, REFRESH and
#: REBUILD stay unreachable - three minutes of that made the dialog look dead
#: and left no way to undo a start.  A node still loading its block index after
#: this is reported as such, and the panel's own 3 s polling picks it up when
#: it finally answers.
_START_WAIT_TIMEOUT = 45.0


def _directory_size(path: Path) -> int:
    """Sum a directory's files.  Metadata only - fast even on a 15 GB tree."""
    if not path.is_dir():
        return 0
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except OSError:                            # pragma: no cover - races, perms
        pass
    return total


def probe_disk(settings) -> DiskEvent:
    """
    Free space where the UTXO database lives, plus a rebuild estimate.

    A rebuild is not a swap: partway through, the old database, the new one
    being built, and the CSV export are all on disk at once.  The old one is
    only removed at the very end, so peak usage is what matters - and it is
    several times the final size.
    """
    from mining_dark import bitcoin_node

    db_file = settings.utxo.resolved_db_file()
    target = db_file.parent
    target.mkdir(parents=True, exist_ok=True)

    usage = shutil.disk_usage(target)
    current_db = db_file.stat().st_size if db_file.exists() else 0

    try:
        chainstate = _directory_size(bitcoin_node.active_chainstate_dir())
    except Exception:                          # noqa: BLE001 - no node is fine
        chainstate = 0

    # What the last rebuild actually cost beats any guess, so it is used when
    # there is one on record.  The guess it replaces read the chainstate as the
    # size of the database it produces - 12.66 GB against a real 3.12 GB - and
    # a progress bar divided by that number ends the import phase a quarter
    # full, which reads as a rebuild that stalled.
    measured_csv, measured_db = _last_rebuild_sizes(db_file)

    # Falling back: a database comes out at roughly a quarter of the chainstate
    # it was dumped from, and the CSV at around 0.7 of it - plain text per UTXO
    # against LevelDB's compressed records.  Both measured on mainnet at height
    # 961,897; order of magnitude, not a promise.
    new_db = measured_db or int(chainstate * _DB_TO_CHAINSTATE) or current_db
    csv_dump = measured_csv or int(chainstate * _CSV_TO_CHAINSTATE)
    estimate = current_db + new_db + csv_dump

    return DiskEvent(
        path=str(target),
        free_bytes=usage.free,
        total_bytes=usage.total,
        estimated_rebuild_bytes=estimate,
        # Broken out so a rebuild in flight can be turned into a progress
        # fraction: the panel compares these against the temp files on disk.
        estimated_csv_bytes=csv_dump,
        estimated_db_bytes=new_db,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  UTXO database
# ═══════════════════════════════════════════════════════════════════════════════
def rebuild_utxo(
    bus: EventBus,
    settings,
    *,
    force: bool = False,
    from_snapshot: bool = False,
) -> None:
    """
    Rebuild the local UTXO database from Bitcoin Core.

    `from_snapshot` exports from a loaded assumeutxo snapshot with the node
    stopped - the way through when bitcoind cannot start but the snapshot on
    disk is intact.  See `utxo_updater.update_from_node`.

    `utxo_updater` renders its own Rich progress bar to stdout, which cannot be
    piped into the log panel without turning ANSI redraw frames into garbage -
    so the panel logs the start and the outcome, and the terminal keeps the
    detailed progress.
    """
    from mining_dark import utxo_updater

    disk = probe_disk(settings)
    if not disk.sufficient:
        # A warning, not a refusal: the estimate is derived from the chainstate
        # size and can be wrong in either direction.  Refusing on a bad guess
        # would be worse than the problem it prevents.
        bus.emit(_log(LogLevel.WARNING, "log.disk_tight",
                      free=_gb(disk.free_bytes),
                      need=_gb(disk.estimated_rebuild_bytes)))

    bus.emit(_log(LogLevel.WARNING, "log.utxo_rebuild_started",
                  file_level=LogLevel.INFO))
    utxo_updater.update_from_node(force=force, from_snapshot=from_snapshot)
    bus.emit(_log(LogLevel.SUCCESS, "log.utxo_rebuild_done"))
    bus.emit(read_database(settings))
    bus.emit(probe_disk(settings))


# ═══════════════════════════════════════════════════════════════════════════════
#  assumeutxo snapshot
# ═══════════════════════════════════════════════════════════════════════════════
#: Set while a download is in flight so the panel can offer to stop it.  The
#: download is hours long; without a way out the only cancel was killing the
#: application, which is how partial files got orphaned in the first place.
_snapshot_stop = threading.Event()


def snapshot_download_cancelled() -> bool:
    return _snapshot_stop.is_set()


def cancel_snapshot_download() -> None:
    """Ask the running download to stop.  What is on disk stays and resumes."""
    _snapshot_stop.set()


def download_snapshot(bus: EventBus) -> None:
    """
    Fetch the assumeutxo snapshot, resuming whatever is already downloaded.

    Runs for a long time - hand it to the TaskRunner, never the render thread.
    Progress is not published as events: the panel reads the file size straight
    off disk, the same way it follows a UTXO rebuild, which keeps this free of
    per-chunk event traffic.
    """
    from mining_dark import snapshot

    _snapshot_stop.clear()
    bus.emit(_log(LogLevel.INFO, "log.snapshot_download_started"))

    try:
        path = snapshot.download(should_stop=_snapshot_stop.is_set)
    except snapshot.SnapshotError as exc:
        bus.emit(_log(LogLevel.ERROR, "log.snapshot_download_failed", error=str(exc)))
        return

    expected = snapshot.remote_size(snapshot.mirror_urls()[0])
    if snapshot.is_complete(path, expected):
        bus.emit(_log(LogLevel.SUCCESS, "log.snapshot_download_done", path=str(path)))
    else:
        bus.emit(_log(LogLevel.WARNING, "log.snapshot_download_paused",
                      done=_gb(snapshot.local_size(path)), total=_gb(expected)))


def load_snapshot(bus: EventBus) -> None:
    """
    Hand the downloaded snapshot to Bitcoin Core.

    Blocks for tens of minutes to hours while Core deserialises every coin and
    checks the set against the hash in its own binary.
    """
    from mining_dark import bitcoin_node, snapshot

    path = snapshot.snapshot_path()
    expected = snapshot.remote_size(snapshot.mirror_urls()[0])
    if not snapshot.is_complete(path, expected):
        # Loading a truncated file fails hours in, after Core has already
        # rebuilt a chainstate directory it then has to throw away.
        bus.emit(_log(LogLevel.ERROR, "log.snapshot_incomplete",
                      done=_gb(snapshot.local_size(path)), total=_gb(expected)))
        return

    bus.emit(_log(LogLevel.WARNING, "log.snapshot_load_started",
                  file_level=LogLevel.INFO))
    try:
        bitcoin_node.load_snapshot(path)
    except bitcoin_node.BitcoinNodeError as exc:
        bus.emit(_log(LogLevel.ERROR, "log.snapshot_load_failed", error=str(exc)))
        return

    bus.emit(_log(LogLevel.SUCCESS, "log.snapshot_load_done"))
    bus.emit(probe_node())


def _gb(value: int) -> str:
    """Bytes as GB, grouped like every other number in the project."""
    return f"{value / 1e9:,.1f} GB"


def read_database(settings):
    """Read the UTXO database health without holding the connection open."""
    from mining_dark.gui.state import DatabaseEvent, DBStatus
    from mining_dark.utils.utxo_db import UTXODatabase

    with UTXODatabase(settings.utxo.resolved_db_file()) as db:
        status = {
            "ok": DBStatus.OK,
            "outdated": DBStatus.OUTDATED,
            "missing": DBStatus.MISSING,
        }.get(db.status, DBStatus.UNKNOWN)
        last = db.last_updated
        return DatabaseEvent(
            status=status,
            address_count=db.address_count,
            size_mb=db.db_size_mb,
            last_updated=last.strftime("%d/%m/%Y %H:%M") if last else "-",
            source=db.source,
            age_days=db.age_days,
        )


# ═══════════════════════════════════════════════════════════════════════════════
#  Desktop integration
# ═══════════════════════════════════════════════════════════════════════════════
def open_in_desktop(target: Path) -> None:
    """
    Hand a file or folder to the desktop's default handler.

    Used instead of rendering wallet files in-app: the private keys they contain
    then never pass through this process's framebuffer, so they cannot end up in
    a screenshot or a screen share of the dashboard.
    """
    target = Path(target)
    if not target.exists():
        raise FileNotFoundError(str(target))

    system = platform.system()
    if system == "Darwin":
        subprocess.Popen(["open", str(target)])
    elif system == "Windows":                      # pragma: no cover - not our target
        os.startfile(str(target))                  # type: ignore[attr-defined]
    else:
        subprocess.Popen(
            ["xdg-open", str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
