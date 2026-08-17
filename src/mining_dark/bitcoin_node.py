"""
Wrapper around Bitcoin Core pinned to the project's datadir.

Every `bitcoind` and `bitcoin-cli` invocation goes through here rather than
straight to the shell, so `-datadir` always points at `data/bitcoin-core/`.
Without it the node silently falls back to `~/.bitcoin/` (Linux) or
`~/Library/Application Support/Bitcoin` (macOS).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mining_dark import paths
from mining_dark.i18n import t


class BitcoinNodeError(RuntimeError):
    """Raised when a bitcoind/bitcoin-cli operation fails in an unrecoverable way."""


# Minimum Bitcoin Core version carrying the assumeutxo snapshot parameters we
# rely on.  27.x only shipped height 840,000; 31.1 adds 880k / 910k / 935k.
MIN_SNAPSHOT_VERSION = (31, 1)

# How long after a fork the pid file may still be missing without meaning the
# node failed.
_PID_FILE_GRACE_S = 5.0

# Lines bitcoind writes when it gives up.  Used to explain a failed start
# instead of leaving the user with a node that silently never came up.
_FATAL_LOG_MARKERS = (
    "[error]",
    "Error:",
    "Corrupted block database detected",
    "Aborted block database rebuild",
)


# ----- Command builders ------------------------------------------------------
def bitcoind_cmd(*extra: str) -> list[str]:
    """Base command for bitcoind scoped to our project datadir."""
    return ["bitcoind", f"-datadir={paths.BITCOIN_CORE_DIR}", *extra]


def bitcoin_cli_cmd(*extra: str) -> list[str]:
    """Base command for bitcoin-cli scoped to our project datadir."""
    return ["bitcoin-cli", f"-datadir={paths.BITCOIN_CORE_DIR}", *extra]


# ----- Environment checks ----------------------------------------------------
def require_binaries(*names: str) -> Optional[str]:
    """Return the name of the first missing binary, or None if all are present."""
    for n in names:
        if shutil.which(n) is None:
            return n
    return None


def is_running() -> bool:
    """True if our bitcoind process is currently running against our datadir."""
    pid_file = paths.BITCOIN_CORE_DIR / "bitcoind.pid"
    if not pid_file.exists():
        return False
    try:
        pid = int(pid_file.read_text().strip())
    except (OSError, ValueError):
        return False
    return _pid_is_bitcoind(pid)


def _pid_is_bitcoind(pid: int) -> bool:
    """
    Whether `pid` is a live bitcoind, not merely a live process.

    An unclean exit - kill -9, a power cut, the reboot this project hit on
    2026-08-08 - leaves bitcoind.pid behind holding a stale pid, and on the next
    boot the OS reuses low pids freely.  `os.kill(pid, 0)` alone would then
    report "running" for whatever unrelated process inherited the number, so
    start() refuses to launch and the panel/doctor show a node that isn't there.
    On Linux /proc/<pid>/cmdline settles it by naming the actual program; where
    there is no /proc (other platforms) liveness is the best signal available,
    so fall back to it rather than refuse a genuinely running node.
    """
    try:
        os.kill(pid, 0)               # cheap liveness probe; does not signal
    except OSError:
        return False                  # no such process, or not ours to signal

    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True                   # no /proc (non-Linux): trust liveness
    if not cmdline:
        return True                   # kernel thread and the like: nothing to read
    return b"bitcoind" in cmdline


# ----- Node lifecycle --------------------------------------------------------
#: What Core writes when startup verification cannot disconnect the last
#: blocks.  On a pruned node this is usually not corruption at all - see
#: `pruned_verify_failure`.
_VERIFY_FAILURE_MARKER = "coin database inconsistencies found"

#: `-checklevel` that skips the two passes needing undo data.  Level 1 still
#: reads every checked block off disk and validates it; only "disconnect" and
#: "reconnect" - the parts that read the .rev files pruning deletes - are left
#: out.  Not "skip the checks": the checks that can be done still are.
SHALLOW_CHECK_LEVEL = 1


def start(reindex: bool = False, check_level: Optional[int] = None) -> None:
    """
    Start bitcoind in the project datadir.  With daemon=1 in bitcoin.conf,
    the process forks and returns immediately.

    `check_level` overrides `-checklevel` for this start.  It exists for the
    pruned-node failure `pruned_verify_failure()` describes, where the default
    level 3 cannot run and Core reports that as a corrupt database.
    """
    missing = require_binaries("bitcoind")
    if missing:
        raise BitcoinNodeError(t("node.err.missing_binary_setup", name=missing))

    if is_running():
        raise BitcoinNodeError(t("node.err.already_running"))

    paths.BITCOIN_CORE_DIR.mkdir(parents=True, exist_ok=True)

    conf = paths.BITCOIN_CORE_DIR / "bitcoin.conf"
    if not conf.exists():
        raise BitcoinNodeError(t("node.err.no_conf", path=conf))

    flags = ["-reindex"] if reindex else []
    if check_level is not None:
        flags.append(f"-checklevel={check_level}")

    cmd = bitcoind_cmd(*flags)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise BitcoinNodeError(
            f"bitcoind falhou ao iniciar (exit {result.returncode}):\n{result.stderr}"
        )


def conf_values() -> dict:
    """
    bitcoin.conf as a flat key -> value mapping.  Empty if it cannot be read.

    Deliberately naive - comments and blank lines out, first `=` splits.  It
    reads the few settings the project cares about, not the whole grammar Core
    accepts (sections, includeconf, network prefixes).
    """
    conf = paths.BITCOIN_CORE_DIR / "bitcoin.conf"
    try:
        text = conf.read_text()
    except OSError:
        return {}

    values: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def prune_target_mib() -> int:
    """The configured prune budget in MiB; 0 when pruning is off or unset."""
    try:
        return int(conf_values().get("prune", "0"))
    except ValueError:
        return 0


def is_pruned() -> bool:
    """Whether bitcoin.conf configures a pruned node."""
    return prune_target_mib() > 0


def pruned_verify_failure(max_bytes: int = 64_000, max_age_s: float = 300.0) -> bool:
    """
    Whether the *last* start died on verification that pruning made impossible.

    Core's startup check disconnects the last few blocks, which reads the undo
    files - and pruning deletes those.  It reports the result as "Corrupted
    block database detected. Please restart with -reindex", which on a pruned
    node is both wrong and expensive: the data is intact, and a reindex throws
    away the whole download to rebuild from blocks that are no longer there.

    Seen for real with `prune=2048` during an assumeutxo background sync: Core
    was holding recent blocks at the tip *and* 2015-era blocks fetched for
    background validation, and the 2 GiB budget could not keep the undo data
    for both.  Starting with a shallower `-checklevel` brought the same node up
    at the correct tip, untouched.

    Age-bounded, like `last_startup_error`.  debug.log keeps every failure the
    node ever had, and without a bound the fix itself does not clear it: the
    node comes up clean and still gets accused of the failure it no longer has
    - and, worse, a later unrelated crash would be retried shallower on the
    strength of a stale line.
    """
    if not is_pruned():
        return False

    for line in reversed(_log_tail(max_bytes).splitlines()):
        if _VERIFY_FAILURE_MARKER not in line:
            continue
        stamp, _, _ = line.partition("Z ")
        return _within(stamp, max_age_s)
    return False


def wait_until_ready(timeout: float = 180.0, poll: float = 0.5) -> bool:
    """
    Block until the node answers RPC.  False if it died or ran out of time.

    `start()` returns as soon as bitcoind forks, which is long before the block
    index is loaded - and a node that gives up during startup (a corrupted
    chainstate being the usual cause) forks successfully too.  Reporting
    "started" off `start()` alone tells the user the opposite of what happened.

    Callers should check `is_running()` on a False to tell "still starting"
    apart from "exited".
    """
    deadline = time.monotonic() + timeout
    # The pid file appears a moment after the fork, so an early absence is not
    # yet evidence of failure.
    settled = time.monotonic() + _PID_FILE_GRACE_S

    while time.monotonic() < deadline:
        if getblockchaininfo() is not None:
            return True
        if not is_running() and time.monotonic() > settled:
            return False
        time.sleep(poll)

    return False


def _log_tail(max_bytes: int) -> str:
    """
    The last `max_bytes` of debug.log, or "" if it cannot be read.

    Everything that reads the log wants the end of it - debug.log routinely
    runs to tens of megabytes, and every phase these functions report on is the
    one happening now.
    """
    log = paths.BITCOIN_CORE_DIR / "debug.log"
    try:
        with open(log, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - max_bytes))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def last_startup_error(max_bytes: int = 64_000, max_age_s: float = 300.0) -> str:
    """
    The most recent error bitcoind logged, if it is recent enough to describe
    what is happening now.

    Bounded by age on purpose.  debug.log keeps every failure the node ever
    had, so without this an unrelated crash from hours ago gets reported as a
    fresh refusal to start - the panel then greets the user with an error about
    an attempt they never made, and advises them not to repeat a click they
    never performed.

    Reads only the tail: debug.log routinely runs to tens of megabytes.
    """
    tail = _log_tail(max_bytes)

    for line in reversed(tail.splitlines()):
        if not any(marker in line for marker in _FATAL_LOG_MARKERS):
            continue
        stamp, _, message = line.partition("Z ")
        if not _within(stamp, max_age_s):
            return ""
        # The timestamp is dropped: the panel has no room for it.
        return message.strip() or line.strip()
    return ""


_CHAINSTATE_HEIGHT_RE = re.compile(r"Chainstate \[snapshot\] @ height (\d+)")

#: Core logs both header passes in the same shape: a pre-synchronisation sweep
#: and then the committing sweep.  Both carry their own percentage.
_HEADER_SYNC_RE = re.compile(
    r"(Pre-s|S)ynchronizing blockheaders, height: (\d+) \(~([\d.]+)%\)"
)


def header_sync_progress(max_bytes: int = 32_000) -> Optional[tuple]:
    """
    How far along the header download is, as (fraction, height), or None.

    Headers are the first thing a fresh node does and they take minutes, but
    `getblockchaininfo` reports `blocks: 0, headers: 0` throughout the
    pre-synchronisation pass - so a panel driven by RPC alone shows a dead
    zero while the node is working hard.  The log is the only place this phase
    is visible.
    """
    tail = _log_tail(max_bytes)

    matches = _HEADER_SYNC_RE.findall(tail)
    if not matches:
        return None

    _, height, percent = matches[-1]
    return min(float(percent) / 100.0, 1.0), int(height)


#: The three shapes Core logs while `loadtxoutset` runs.  It deserialises 164
#: million coins over tens of minutes, and the RPC that started it holds
#: cs_main the whole time - so `getblockchaininfo` blocks and the log is the
#: only place the work is visible.  Without this the interface has nothing to
#: show between the click and the end, which is exactly how a load that was
#: working got killed halfway on 2026-08-09.
_SNAPSHOT_LOAD_START_RE = re.compile(r"\[snapshot\] loading (\d+) coins from snapshot")
_SNAPSHOT_LOAD_TICK_RE = re.compile(r"\[snapshot\] (\d+) coins loaded \(([\d.]+)%")
_SNAPSHOT_LOAD_DONE_RE = re.compile(r"\[snapshot\] loaded (\d+) \([^)]*\) coins from snapshot")


def snapshot_load_progress(max_bytes: int = 32_000) -> Optional[tuple]:
    """
    How far `loadtxoutset` has got, as (fraction, coins_done, coins_total).

    None when the log tail holds no load at all.  Reports the *last* load event
    in the tail, whichever kind it is, so a load that starts while the previous
    one's completion line is still in view reads as 0% rather than as finished.

    The total is derived from the tick line itself - `done / percent` - because
    the line naming it is written once, hours before the end, and long gone
    from any tail worth reading.
    """
    state = None

    for line in _log_tail(max_bytes).splitlines():
        if "[snapshot] " not in line:
            continue

        tick = _SNAPSHOT_LOAD_TICK_RE.search(line)
        if tick:
            done, percent = int(tick.group(1)), float(tick.group(2))
            total = round(done * 100.0 / percent) if percent > 0 else 0
            state = (min(percent / 100.0, 1.0), done, total)
            continue

        done_line = _SNAPSHOT_LOAD_DONE_RE.search(line)
        if done_line:
            coins = int(done_line.group(1))
            state = (1.0, coins, coins)
            continue

        start = _SNAPSHOT_LOAD_START_RE.search(line)
        if start:
            state = (0.0, 0, int(start.group(1)))

    return state


def snapshot_height_from_log(max_bytes: int = 512_000) -> int:
    """
    The snapshot chainstate's height as the node last reported it, or 0.

    An offline export has no RPC to ask, and `base_blockhash` holds a hash with
    no height beside it.  The node writes the height on every startup, so the
    log is the one source available with bitcoind down - and recording it beats
    leaving the database claiming block height 0.
    """
    tail = _log_tail(max_bytes)

    matches = _CHAINSTATE_HEIGHT_RE.findall(tail)
    return int(matches[-1]) if matches else 0


def _within(stamp: str, max_age_s: float) -> bool:
    """
    Whether a `2026-08-08T17:20:51` log timestamp (UTC) is recent enough.

    An unparseable stamp counts as recent: a log format we do not recognise
    should not silently hide a real failure.
    """
    try:
        when = datetime.strptime(stamp.strip(), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return True
    age = (datetime.now(timezone.utc) - when.replace(tzinfo=timezone.utc)).total_seconds()
    return age <= max_age_s


def stop(timeout: float = 60.0) -> bool:
    """
    Ask bitcoind to stop gracefully and wait up to `timeout` seconds for it
    to exit.  Returns True if the process exited within the timeout.
    """
    missing = require_binaries("bitcoin-cli")
    if missing:
        raise BitcoinNodeError(t("node.err.missing_binary", name=missing))

    if not is_running():
        return True

    subprocess.run(bitcoin_cli_cmd("stop"), capture_output=True, text=True)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_running():
            return True
        time.sleep(0.5)

    return False


# ----- RPC -------------------------------------------------------------------
def rpc_call(method: str, *args: str, timeout: float = 10.0) -> Optional[dict]:
    """
    Invoke `bitcoin-cli <method> [args...]` and return parsed JSON on success.
    Returns None if bitcoin-cli can't reach the node (not running, wrong
    credentials, warming up, etc.).
    """
    if require_binaries("bitcoin-cli"):
        return None

    try:
        result = subprocess.run(
            bitcoin_cli_cmd(method, *args),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None

    if result.returncode != 0:
        return None

    output = result.stdout.strip()
    if not output:
        return None
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return {"_raw": output}


def getblockchaininfo() -> Optional[dict]:
    return rpc_call("getblockchaininfo")


def getchainstates() -> Optional[dict]:
    """
    Report every chainstate the node is tracking.  With no snapshot loaded this
    is a single entry; after `loadtxoutset` there are two - the background one
    replaying history from genesis, and the snapshot one serving the tip.

    Returns None on older nodes that predate the RPC (added in Core 28.0).
    """
    return rpc_call("getchainstates")


def invalid_branch() -> Optional[tuple]:
    """
    `(height, hash, length)` of the lowest chain branch Core has marked
    invalid above the active tip, or None when the chain is clean.

    This is the one unambiguous "the node will never advance again" signal.
    Once a block fails to connect, Core records it invalid in the block index
    and refuses it from every peer for the life of the datadir - a restart does
    not clear it, and neither does fixing whatever caused it.  Observed for six
    days straight on a chainstate that had lost a coin: the same block rejected
    413 times, from every peer that offered it.

    Comparing `blocks` against `headers` cannot see this, because Core reports
    `headers` as the best *valid* header chain: with the branch above the tip
    marked invalid, both numbers read 961897 and the node looks caught up.
    """
    tips = rpc_call("getchaintips")
    if not isinstance(tips, list):
        return None

    active = max(
        (int(t_.get("height", 0)) for t_ in tips if t_.get("status") == "active"),
        default=0,
    )
    invalid = [
        (int(t_.get("height", 0)), str(t_.get("hash", "")), int(t_.get("branchlen", 0)))
        for t_ in tips
        if t_.get("status") == "invalid" and int(t_.get("height", 0)) > active
    ]
    return min(invalid) if invalid else None


def peer_block_height() -> int:
    """
    Median block height the connected peers report, 0 when none answered.

    The median rather than the maximum: one peer lying about a huge height, or
    one still catching up, must not decide whether this node is behind.
    """
    peers = rpc_call("getpeerinfo")
    if not isinstance(peers, list):
        return 0

    heights = sorted(
        int(p.get("synced_headers", 0) or 0) for p in peers
        if int(p.get("synced_headers", 0) or 0) > 0
    )
    if not heights:
        return 0
    return heights[len(heights) // 2]


def core_version() -> Optional[tuple[int, ...]]:
    """Installed Bitcoin Core version as a tuple, e.g. (31, 1, 0).  None if unknown."""
    if require_binaries("bitcoin-cli"):
        return None
    try:
        result = subprocess.run(
            ["bitcoin-cli", "--version"], capture_output=True, text=True, timeout=30
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None

    # First line looks like: "Bitcoin Core RPC client version v31.1.0"
    head = result.stdout.splitlines()[0] if result.stdout else ""
    token = head.strip().split()[-1].lstrip("v") if head.strip() else ""
    try:
        return tuple(int(p) for p in token.split(".") if p != "")
    except ValueError:
        return None


# ----- Chainstate resolution -------------------------------------------------
# A running loadtxoutset rewrites the snapshot chainstate constantly (LevelDB
# flushes land every few seconds), so a directory untouched for this long with
# no completion marker is debris, not a load in flight.  Generous on purpose:
# a stalled flush on a slow disk must not be mistaken for an abandoned load.
_LOAD_ACTIVITY_WINDOW_S = 300


def _snapshot_dir_last_write() -> float:
    """Newest mtime among the snapshot chainstate's files; 0.0 if unreadable."""
    try:
        return max(
            (p.stat().st_mtime for p in paths.SNAPSHOT_CHAINSTATE_DIR.iterdir() if p.is_file()),
            default=0.0,
        )
    except OSError:
        return 0.0


def snapshot_dir_state() -> str:
    """
    Classify `chainstate_snapshot/`: 'none' | 'loading' | 'orphaned' | 'loaded'.

    The directory existing proves nothing - an interrupted `loadtxoutset` leaves
    gigabytes of half-written LevelDB behind, and Core ignores it ("snapshot
    chainstate dir is malformed") while quietly carrying on with a normal IBD in
    `chainstate/`.  Treating that leftover as the live tip is how a truncated
    UTXO set gets dumped into the scanner's database.

    `base_blockhash` is written only when a load finishes, so it separates
    'loaded' from the rest.  Nothing in the RPC helps below that line: during a
    load `getchainstates` still reports a single chainstate, exactly like debris
    does - Core registers the second one only at the end.  What does separate
    them is that a live load keeps writing, so recent mtimes stand in for it.
    """
    if not paths.SNAPSHOT_CHAINSTATE_DIR.is_dir():
        return "none"
    if paths.SNAPSHOT_BASE_HASH_FILE.is_file():
        return "loaded"
    if is_running() and (time.time() - _snapshot_dir_last_write()) < _LOAD_ACTIVITY_WINDOW_S:
        return "loading"
    return "orphaned"


def active_chainstate_dir() -> Path:
    """
    The LevelDB directory holding the UTXO set that corresponds to the node's
    current tip.

    Normally that is `chainstate/`.  While an assumeutxo background sync is in
    flight, Bitcoin Core serves the tip from `chainstate_snapshot/` and keeps
    `chainstate/` pinned at whatever height the background validation reached -
    so reading `chainstate/` there would silently yield a years-old UTXO set.
    Once background validation finishes, Core promotes the snapshot back to
    `chainstate/` and removes the suffixed directory.

    Only a *completed* snapshot load serves the tip; for anything else the node
    is on `chainstate/`, so that is what this returns.  Callers that read the
    result as a UTXO set must still check snapshot_dir_state() - see
    require_dumpable_chainstate().
    """
    if snapshot_dir_state() == "loaded":
        return paths.SNAPSHOT_CHAINSTATE_DIR
    return paths.CHAINSTATE_DIR


def require_dumpable_chainstate() -> Path:
    """
    active_chainstate_dir(), but refuses the states where the directory on disk
    doesn't hold a coherent UTXO set at the tip.  Raises BitcoinNodeError.
    """
    state = snapshot_dir_state()

    if state == "loading":
        raise BitcoinNodeError(t("node.err.load_in_progress"))

    if state == "orphaned":
        raise BitcoinNodeError(
            t("node.err.orphaned_chainstate", path=paths.SNAPSHOT_CHAINSTATE_DIR)
        )

    return active_chainstate_dir()


def snapshot_in_progress() -> bool:
    """
    True while a *completed* snapshot serves the tip, i.e. background
    validation from genesis hasn't caught up yet.

    An aborted load doesn't count - see snapshot_dir_state().
    """
    return snapshot_dir_state() == "loaded"


def snapshot_status() -> Optional[dict]:
    """
    Summarise assumeutxo state for display:

        {"active": bool, "snapshot_blockhash": str|None,
         "tip_blocks": int, "background_blocks": int, "validated": bool}

    Returns None if the node isn't reachable or the RPC isn't available.
    """
    info = getchainstates()
    if not info:
        return None

    states = info.get("chainstates") or []
    if not states:
        return None

    # Ordered by cumulative work, most-work last - that one serves the tip.
    tip = states[-1]
    background = states[0] if len(states) > 1 else None

    return {
        "active": len(states) > 1,
        "snapshot_blockhash": tip.get("snapshot_blockhash"),
        "tip_blocks": tip.get("blocks", 0),
        "background_blocks": background.get("blocks", 0) if background else 0,
        "validated": tip.get("validated", True),
        "headers": info.get("headers", 0),
    }


# ----- assumeutxo ------------------------------------------------------------
# Snapshot files are published as `utxo-<height>.dat`.  The height is only used
# for a friendlier preflight error, so an unrecognised name is not fatal.
_SNAPSHOT_HEIGHT_RE = re.compile(r"utxo-(\d+)\.dat$")


def _require_headers_for(snapshot: Path) -> None:
    """
    Refuse to call `loadtxoutset` before the header chain reaches the snapshot.

    Core can only anchor a snapshot to a block header it already knows about, so
    calling this right after `node start` fails with an opaque RPC error.  Header
    sync only takes a few minutes, but the failure looks identical to a corrupt
    file, which sends people down the wrong path.
    """
    match = _SNAPSHOT_HEIGHT_RE.search(snapshot.name)
    if match is None:
        return

    height = int(match.group(1))
    info = getblockchaininfo()
    if info is None:
        return

    headers = info.get("headers")
    if not isinstance(headers, int) or headers >= height:
        return

    raise BitcoinNodeError(
        t("node.err.headers_behind", headers=f"{headers:,}", height=f"{height:,}")
    )


def load_snapshot(snapshot: Path) -> dict:
    """
    Load an assumeutxo UTXO snapshot via `loadtxoutset`.

    Bitcoin Core validates the snapshot against a hash hardcoded in its own
    chainparams, so a corrupted or hostile file is rejected rather than trusted
    - the file's origin doesn't have to be trusted, only its contents.

    This blocks for tens of minutes to a few hours.  `-rpcclienttimeout=0`
    disables the client-side timeout so bitcoin-cli waits instead of giving up
    mid-load, and no subprocess timeout is set here for the same reason.
    """
    missing = require_binaries("bitcoin-cli")
    if missing:
        raise BitcoinNodeError(t("node.err.missing_binary", name=missing))

    if not snapshot.is_file():
        raise BitcoinNodeError(t("node.err.snapshot_file", path=snapshot))

    # bitcoind resolves a relative path against its own datadir, not our cwd, so
    # `data/snapshots/x.dat` would be looked up under data/bitcoin-core/.  We
    # check the file relative to cwd above, so send the absolute path it matched.
    snapshot = snapshot.resolve()

    if not is_running():
        raise BitcoinNodeError(t("node.err.not_running"))

    version = core_version()
    if version is not None and version[:2] < MIN_SNAPSHOT_VERSION:
        got = ".".join(str(p) for p in version)
        want = ".".join(str(p) for p in MIN_SNAPSHOT_VERSION)
        raise BitcoinNodeError(t("node.err.core_too_old", got=got, want=want))

    state = snapshot_dir_state()
    if state == "loaded":
        raise BitcoinNodeError(t("node.err.snapshot_loaded"))
    if state == "loading":
        raise BitcoinNodeError(t("node.err.snapshot_loading"))
    if state == "orphaned":
        # Refusing here on the mere existence of the directory is what stranded
        # the retry before: the leftover of a killed load blocked the reload
        # that would have fixed it.
        raise BitcoinNodeError(
            t("node.err.snapshot_orphaned", path=paths.SNAPSHOT_CHAINSTATE_DIR)
        )

    _require_headers_for(snapshot)

    result = subprocess.run(
        bitcoin_cli_cmd("-rpcclienttimeout=0", "loadtxoutset", str(snapshot)),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise BitcoinNodeError(
            f"loadtxoutset falhou (exit {result.returncode}):\n{result.stderr.strip()}"
        )

    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {"_raw": result.stdout.strip()}


# ----- Passthrough for `mining-dark node cli ...` ----------------------------
def run_cli_passthrough(args: list[str]) -> int:
    """
    Forward args to `bitcoin-cli -datadir=<...> <args...>`, streaming output
    directly to the current stdout/stderr.  Returns the child's exit code.
    """
    if require_binaries("bitcoin-cli"):
        raise BitcoinNodeError(
            t("node.err.missing_binary_setup", name="bitcoin-cli")
        )
    return subprocess.call(bitcoin_cli_cmd(*args))
