"""bitcoin_node command builders must always scope to the project datadir."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
import time
from pathlib import Path

import pytest

from mining_dark import bitcoin_node, paths


def test_bitcoind_cmd_contains_project_datadir() -> None:
    cmd = bitcoin_node.bitcoind_cmd()
    assert cmd[0] == "bitcoind"
    assert any(arg == f"-datadir={paths.BITCOIN_CORE_DIR}" for arg in cmd)
    # datadir must point INSIDE the project - never ~/.bitcoin
    assert str(paths.BITCOIN_CORE_DIR).startswith(str(paths.PROJECT_ROOT))


def test_bitcoin_cli_cmd_forwards_extra_args() -> None:
    cmd = bitcoin_node.bitcoin_cli_cmd("getblockchaininfo", "-help")
    assert cmd[0] == "bitcoin-cli"
    assert cmd[-2:] == ["getblockchaininfo", "-help"]


def test_require_binaries_returns_missing() -> None:
    # A binary that definitely doesn't exist
    missing = bitcoin_node.require_binaries("this-binary-does-not-exist-xyz-42")
    assert missing == "this-binary-does-not-exist-xyz-42"


def test_require_binaries_returns_none_when_present() -> None:
    # `sh` exists on every POSIX system
    assert bitcoin_node.require_binaries("sh") is None


def test_load_snapshot_cmd_disables_client_timeout() -> None:
    """loadtxoutset runs for hours - the CLI must not time out mid-load."""
    cmd = bitcoin_node.bitcoin_cli_cmd("-rpcclienttimeout=0", "loadtxoutset", "/tmp/x.dat")
    assert "-rpcclienttimeout=0" in cmd
    assert cmd.index("-rpcclienttimeout=0") < cmd.index("loadtxoutset")


def _reload_with_datadir(monkeypatch, tmp_path):
    import importlib
    monkeypatch.setenv("MINING_DARK_DATA_DIR", str(tmp_path))
    importlib.reload(paths)
    importlib.reload(bitcoin_node)


def _restore(monkeypatch):
    import importlib
    monkeypatch.delenv("MINING_DARK_DATA_DIR", raising=False)
    importlib.reload(paths)
    importlib.reload(bitcoin_node)


def test_active_chainstate_is_plain_dir_without_snapshot(tmp_path, monkeypatch) -> None:
    _reload_with_datadir(monkeypatch, tmp_path)
    try:
        (tmp_path / "bitcoin-core" / "chainstate").mkdir(parents=True)
        assert bitcoin_node.active_chainstate_dir() == paths.CHAINSTATE_DIR
        assert bitcoin_node.snapshot_in_progress() is False
    finally:
        _restore(monkeypatch)


def _make_snapshot_dir(tmp_path, *, loaded: bool, stale: bool = False):
    """
    Create chainstate/ + chainstate_snapshot/ in one of the shapes Core leaves
    behind.  `stale` backdates the LevelDB writes past the activity window, which
    is what tells debris from a load still in flight.
    """
    (tmp_path / "bitcoin-core" / "chainstate").mkdir(parents=True, exist_ok=True)
    snap = tmp_path / "bitcoin-core" / "chainstate_snapshot"
    snap.mkdir(parents=True, exist_ok=True)
    (snap / "000001.ldb").write_bytes(b"x")
    if loaded:
        (snap / "base_blockhash").write_bytes(b"\x00" * 32)
    if stale:
        old = time.time() - bitcoin_node._LOAD_ACTIVITY_WINDOW_S - 60
        for entry in snap.iterdir():
            os.utime(entry, (old, old))
    return snap


def test_active_chainstate_prefers_snapshot_dir(tmp_path, monkeypatch) -> None:
    """
    With an assumeutxo background sync running, `chainstate/` is pinned at an old
    height while the tip lives in `chainstate_snapshot/`.  Dumping the wrong one
    would build a UTXO database that is silently years out of date.
    """
    _reload_with_datadir(monkeypatch, tmp_path)
    try:
        _make_snapshot_dir(tmp_path, loaded=True)
        assert bitcoin_node.active_chainstate_dir() == paths.SNAPSHOT_CHAINSTATE_DIR
        assert bitcoin_node.snapshot_in_progress() is True
        assert bitcoin_node.snapshot_dir_state() == "loaded"
        assert bitcoin_node.require_dumpable_chainstate() == paths.SNAPSHOT_CHAINSTATE_DIR
    finally:
        _restore(monkeypatch)


def test_aborted_load_is_not_treated_as_the_tip(tmp_path, monkeypatch) -> None:
    """
    A `loadtxoutset` killed midway leaves a populated chainstate_snapshot/ with no
    base_blockhash.  Core ignores it and keeps doing a normal IBD in chainstate/,
    so reading it as the tip would dump a half-written UTXO set - the export must
    refuse rather than silently build a truncated database.
    """
    _reload_with_datadir(monkeypatch, tmp_path)
    try:
        # The node is back up and doing a normal IBD - exactly the shape a crash
        # mid-load leaves, and the one that used to read as a live snapshot.
        _make_snapshot_dir(tmp_path, loaded=False, stale=True)
        monkeypatch.setattr(bitcoin_node, "is_running", lambda: True)

        assert bitcoin_node.snapshot_dir_state() == "orphaned"
        # Core is on chainstate/, so that is the honest answer for display.
        assert bitcoin_node.active_chainstate_dir() == paths.CHAINSTATE_DIR
        assert bitcoin_node.snapshot_in_progress() is False

        with pytest.raises(bitcoin_node.BitcoinNodeError, match="base_blockhash"):
            bitcoin_node.require_dumpable_chainstate()
    finally:
        _restore(monkeypatch)


def test_load_in_flight_blocks_the_dump(tmp_path, monkeypatch) -> None:
    """Same directory shape as an aborted load, but bitcoind is up: a load is running."""
    _reload_with_datadir(monkeypatch, tmp_path)
    try:
        _make_snapshot_dir(tmp_path, loaded=False)
        monkeypatch.setattr(bitcoin_node, "is_running", lambda: True)

        assert bitcoin_node.snapshot_dir_state() == "loading"
        with pytest.raises(bitcoin_node.BitcoinNodeError, match="andamento"):
            bitcoin_node.require_dumpable_chainstate()
    finally:
        _restore(monkeypatch)


def test_load_snapshot_retry_allowed_after_aborted_load(tmp_path, monkeypatch) -> None:
    """
    Refusing on the mere existence of chainstate_snapshot/ stranded the retry: the
    debris of a killed load blocked the reload that would have cleared it.  The
    error must name the fix instead.
    """
    _reload_with_datadir(monkeypatch, tmp_path)
    try:
        _make_snapshot_dir(tmp_path, loaded=False, stale=True)
        monkeypatch.setattr(bitcoin_node, "is_running", lambda: True)
        monkeypatch.setattr(bitcoin_node, "require_binaries", lambda *a: None)
        monkeypatch.setattr(bitcoin_node, "core_version", lambda: (31, 1, 0))

        snapshot = tmp_path / "utxo-935000.dat"
        snapshot.touch()
        with pytest.raises(bitcoin_node.BitcoinNodeError, match="rm -rf"):
            bitcoin_node.load_snapshot(snapshot)
    finally:
        _restore(monkeypatch)


def test_is_running_false_when_no_pidfile(tmp_path, monkeypatch) -> None:
    """If bitcoind.pid doesn't exist, is_running() must return False."""
    import importlib
    monkeypatch.setenv("MINING_DARK_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MINING_DARK_DATA_DIR", raising=False)  # will reset after

    monkeypatch.setenv("MINING_DARK_DATA_DIR", str(tmp_path))
    importlib.reload(paths)
    importlib.reload(bitcoin_node)

    assert bitcoin_node.is_running() is False

    monkeypatch.delenv("MINING_DARK_DATA_DIR")
    importlib.reload(paths)
    importlib.reload(bitcoin_node)


def test_is_running_rejects_a_live_but_unrelated_pid(tmp_path, monkeypatch) -> None:
    """
    A pid file left by an unclean exit keeps a stale pid; after a reboot the OS
    can reuse it for something that is not bitcoind.  is_running() must check the
    process actually is bitcoind, not just that the pid is alive.
    """
    import os

    monkeypatch.setattr(bitcoin_node.paths, "BITCOIN_CORE_DIR", tmp_path)
    # This test process is alive and plainly not bitcoind.
    (tmp_path / "bitcoind.pid").write_text(str(os.getpid()))

    assert bitcoin_node.is_running() is False


def test_headers_preflight_blocks_when_behind_snapshot(tmp_path, monkeypatch) -> None:
    """Calling loadtxoutset before header sync fails opaquely - catch it early."""
    import pytest

    snapshot = tmp_path / "utxo-935000.dat"
    snapshot.touch()
    monkeypatch.setattr(bitcoin_node, "getblockchaininfo", lambda: {"headers": 800_000})

    with pytest.raises(bitcoin_node.BitcoinNodeError, match="935,000"):
        bitcoin_node._require_headers_for(snapshot)


def test_headers_preflight_passes_when_ahead(tmp_path, monkeypatch) -> None:
    snapshot = tmp_path / "utxo-935000.dat"
    snapshot.touch()
    monkeypatch.setattr(bitcoin_node, "getblockchaininfo", lambda: {"headers": 940_000})

    bitcoin_node._require_headers_for(snapshot)  # must not raise


def test_headers_preflight_skips_unrecognised_filename(tmp_path, monkeypatch) -> None:
    """A custom filename carries no height - don't block the user over it."""
    snapshot = tmp_path / "my-snapshot.dat"
    snapshot.touch()

    def _boom():
        raise AssertionError("must not query the node for an unparsable name")

    monkeypatch.setattr(bitcoin_node, "getblockchaininfo", _boom)
    bitcoin_node._require_headers_for(snapshot)  # must not raise


def test_load_snapshot_sends_absolute_path(tmp_path, monkeypatch) -> None:
    """bitcoind resolves relative paths against its datadir, not our cwd."""
    snapshot = tmp_path / "utxo-935000.dat"
    snapshot.touch()
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(bitcoin_node, "require_binaries", lambda *a: None)
    monkeypatch.setattr(bitcoin_node, "is_running", lambda: True)
    monkeypatch.setattr(bitcoin_node, "core_version", lambda: (31, 1, 0))
    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "none")
    monkeypatch.setattr(bitcoin_node, "getblockchaininfo", lambda: {"headers": 961_337})

    seen: list[str] = []

    class _Result:
        returncode = 0
        stdout = "{}"
        stderr = ""

    def _fake_run(cmd, **kwargs):
        seen.extend(cmd)
        return _Result()

    monkeypatch.setattr(bitcoin_node.subprocess, "run", _fake_run)

    bitcoin_node.load_snapshot(Path("utxo-935000.dat"))

    passed = seen[seen.index("loadtxoutset") + 1]
    assert Path(passed).is_absolute(), f"caminho relativo enviado ao bitcoind: {passed}"
    assert passed == str(snapshot.resolve())


def test_dat_cleanup_only_offered_after_a_completed_load(tmp_path, monkeypatch) -> None:
    """
    The .dat is the only way to retry a load that didn't finish, so the offer to
    delete it must be gated on the load having completed - not on the snapshot
    directory merely existing, which is true throughout a failed attempt too.
    """
    from mining_dark import cli

    snapshot = tmp_path / "utxo-935000.dat"
    snapshot.write_bytes(b"x" * 1024)

    for state in ("none", "loading", "orphaned"):
        monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda s=state: s)
        cli._offer_snapshot_cleanup(snapshot, remove=True)
        assert snapshot.exists(), f"apagou o .dat no estado {state!r}"

    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "loaded")
    cli._offer_snapshot_cleanup(snapshot, remove=True)
    assert not snapshot.exists()


def test_dat_kept_when_declined(tmp_path, monkeypatch) -> None:
    """Declining must leave the file alone, not fall through to deleting it."""
    from mining_dark import cli

    snapshot = tmp_path / "utxo-935000.dat"
    snapshot.write_bytes(b"x" * 1024)
    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "loaded")

    cli._offer_snapshot_cleanup(snapshot, remove=False)
    assert snapshot.exists()


# ═══════════════════════════════════════════════════════════════════════════════
#  Starting the node must report what actually happened
#
#  bitcoind forks and returns 0 long before the block index is loaded - and a
#  node that quits during startup forks just as successfully.  Reporting off
#  start() alone showed a running node that had already exited.
# ═══════════════════════════════════════════════════════════════════════════════
def test_wait_until_ready_true_once_rpc_answers(monkeypatch) -> None:
    answers = iter([None, None, {"blocks": 1}])
    monkeypatch.setattr(bitcoin_node, "getblockchaininfo", lambda: next(answers))
    monkeypatch.setattr(bitcoin_node, "is_running", lambda: True)

    assert bitcoin_node.wait_until_ready(timeout=5.0, poll=0.0) is True


def test_wait_until_ready_false_when_the_node_dies(monkeypatch) -> None:
    """The corrupted-chainstate case: forked fine, gone a second later."""
    monkeypatch.setattr(bitcoin_node, "getblockchaininfo", lambda: None)
    monkeypatch.setattr(bitcoin_node, "is_running", lambda: False)
    monkeypatch.setattr(bitcoin_node, "_PID_FILE_GRACE_S", 0.0)

    assert bitcoin_node.wait_until_ready(timeout=5.0, poll=0.0) is False


def test_wait_until_ready_tolerates_a_late_pid_file(monkeypatch) -> None:
    """A missing pid file right after the fork is not yet a failure."""
    seen: list[int] = []

    def _running() -> bool:
        seen.append(1)
        return len(seen) > 2          # appears on the third look

    answers = iter([None, None, None, {"blocks": 1}])
    monkeypatch.setattr(bitcoin_node, "getblockchaininfo", lambda: next(answers))
    monkeypatch.setattr(bitcoin_node, "is_running", _running)

    assert bitcoin_node.wait_until_ready(timeout=5.0, poll=0.0) is True


def test_wait_until_ready_gives_up_on_timeout(monkeypatch) -> None:
    monkeypatch.setattr(bitcoin_node, "getblockchaininfo", lambda: None)
    monkeypatch.setattr(bitcoin_node, "is_running", lambda: True)

    assert bitcoin_node.wait_until_ready(timeout=0.05, poll=0.0) is False


def test_last_startup_error_reports_the_reason(tmp_path, monkeypatch) -> None:
    """The last fatal line wins, not the last line."""
    monkeypatch.setattr(paths, "BITCOIN_CORE_DIR", tmp_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    (tmp_path / "debug.log").write_text(
        f"{now}Z Verification progress: 99%\n"
        f"{now}Z [error] Verification error: coin database inconsistencies found\n"
        f"{now}Z Corrupted block database detected.\n"
        f"{now}Z Shutdown done\n",
        encoding="utf-8",
    )

    assert bitcoin_node.last_startup_error() == "Corrupted block database detected."


def test_last_startup_error_is_empty_on_a_clean_log(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "BITCOIN_CORE_DIR", tmp_path)
    (tmp_path / "debug.log").write_text(
        "2026-08-08T16:08:59Z Loaded best chain: height=961599\n", encoding="utf-8"
    )

    assert bitcoin_node.last_startup_error() == ""


def test_last_startup_error_survives_a_missing_log(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "BITCOIN_CORE_DIR", tmp_path)

    assert bitcoin_node.last_startup_error() == ""


# ═══════════════════════════════════════════════════════════════════════════════
#  A failure only describes the present for so long
#
#  debug.log keeps every crash the node ever had.  Reporting the last one
#  unconditionally made the panel greet the user with an error about a start
#  they never attempted - and advise them not to repeat a click they never made.
# ═══════════════════════════════════════════════════════════════════════════════
def _write_log(tmp_path, monkeypatch, when: datetime, message: str) -> None:
    monkeypatch.setattr(paths, "BITCOIN_CORE_DIR", tmp_path)
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
    (tmp_path / "debug.log").write_text(
        f"{stamp}Z Loading block index...\n{stamp}Z {message}\n", encoding="utf-8"
    )


def test_a_fresh_failure_is_reported(tmp_path, monkeypatch) -> None:
    _write_log(tmp_path, monkeypatch, datetime.now(timezone.utc),
               "Corrupted block database detected.")

    assert bitcoin_node.last_startup_error() == "Corrupted block database detected."


def test_an_old_failure_is_not_reported(tmp_path, monkeypatch) -> None:
    """Opening the dialog hours later must not look like a fresh refusal."""
    old = datetime.now(timezone.utc) - timedelta(hours=6)
    _write_log(tmp_path, monkeypatch, old, "Corrupted block database detected.")

    assert bitcoin_node.last_startup_error() == ""


def test_the_age_window_is_the_only_thing_hiding_it(tmp_path, monkeypatch) -> None:
    old = datetime.now(timezone.utc) - timedelta(hours=6)
    _write_log(tmp_path, monkeypatch, old, "Corrupted block database detected.")

    assert bitcoin_node.last_startup_error(max_age_s=86_400) == \
        "Corrupted block database detected."


def test_an_unreadable_timestamp_does_not_hide_the_error(tmp_path, monkeypatch) -> None:
    """A log format we do not recognise must not silently swallow a failure."""
    monkeypatch.setattr(paths, "BITCOIN_CORE_DIR", tmp_path)
    (tmp_path / "debug.log").write_text("[error] something went wrong\n", encoding="utf-8")

    assert "something went wrong" in bitcoin_node.last_startup_error()


# ═══════════════════════════════════════════════════════════════════════════════
#  The header phase is invisible over RPC
#
#  getblockchaininfo reports blocks: 0, headers: 0 for the whole
#  pre-synchronisation pass, so a panel driven by RPC alone shows a dead zero
#  while the node is working.  debug.log is the only place it shows up.
# ═══════════════════════════════════════════════════════════════════════════════
def test_header_progress_is_read_from_the_log(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "BITCOIN_CORE_DIR", tmp_path)
    (tmp_path / "debug.log").write_text(
        "2026-08-08T21:45:28Z Pre-synchronizing blockheaders, height: 298000 (~31.57%)\n"
        "2026-08-08T22:09:12Z Synchronizing blockheaders, height: 226782 (~24.36%)\n",
        encoding="utf-8",
    )

    fraction, height = bitcoin_node.header_sync_progress()

    # The newest line wins, whichever pass it came from.
    assert height == 226_782
    assert fraction == pytest.approx(0.2436)


def test_header_progress_is_none_without_the_phase(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "BITCOIN_CORE_DIR", tmp_path)
    (tmp_path / "debug.log").write_text("2026-08-08T21:45:28Z UpdateTip\n", encoding="utf-8")

    assert bitcoin_node.header_sync_progress() is None


def test_header_progress_survives_a_missing_log(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "BITCOIN_CORE_DIR", tmp_path)

    assert bitcoin_node.header_sync_progress() is None


# ═══════════════════════════════════════════════════════════════════════════════
#  "Corrupted block database" that is not corruption
# ═══════════════════════════════════════════════════════════════════════════════
#  Core's startup check disconnects the last few blocks, which reads the undo
#  files pruning is entitled to delete.  It reports the result as a corrupt
#  database and tells you to -reindex - which on a pruned node discards a
#  working datadir to rebuild from blocks that are no longer on disk.
#
#  Seen for real on 2026-08-10: prune=2048 during an assumeutxo background sync,
#  holding tip blocks and 2015-era blocks at once.  Starting with a shallower
#  -checklevel brought the same node up at the correct tip, untouched.

def _verify_failed_log(age_s: float = 5.0) -> str:
    """The two lines Core writes, stamped `age_s` seconds ago (UTC, like Core)."""
    from datetime import datetime, timedelta, timezone

    when = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
    return (
        f"{stamp}Z [error] Verification error: coin database inconsistencies "
        "found (last 5 blocks, 6959 good transactions before that)\n"
        f"{stamp}Z Corrupted block database detected.\n"
    )


def _conf(tmp_path, body: str, monkeypatch):
    from mining_dark import paths

    monkeypatch.setattr(paths, "BITCOIN_CORE_DIR", tmp_path)
    (tmp_path / "bitcoin.conf").write_text(body)


def test_a_pruned_conf_is_recognised(tmp_path, monkeypatch) -> None:
    from mining_dark import bitcoin_node

    _conf(tmp_path, "prune=20000\nserver=1\n", monkeypatch)

    assert bitcoin_node.is_pruned() is True


def test_prune_zero_is_not_pruned(tmp_path, monkeypatch) -> None:
    from mining_dark import bitcoin_node

    _conf(tmp_path, "prune=0\nserver=1\n", monkeypatch)

    assert bitcoin_node.is_pruned() is False


def test_a_conf_without_prune_is_not_pruned(tmp_path, monkeypatch) -> None:
    from mining_dark import bitcoin_node

    _conf(tmp_path, "server=1\ndaemon=1\n", monkeypatch)

    assert bitcoin_node.is_pruned() is False


def test_a_missing_conf_is_not_pruned(tmp_path, monkeypatch) -> None:
    from mining_dark import bitcoin_node, paths

    monkeypatch.setattr(paths, "BITCOIN_CORE_DIR", tmp_path / "nowhere")

    assert bitcoin_node.is_pruned() is False


def test_the_pruned_verify_failure_is_recognised(tmp_path, monkeypatch) -> None:
    from mining_dark import bitcoin_node

    _conf(tmp_path, "prune=20000\n", monkeypatch)
    (tmp_path / "debug.log").write_text(_verify_failed_log())

    assert bitcoin_node.pruned_verify_failure() is True


def test_the_same_failure_on_a_full_node_is_taken_at_face_value(
        tmp_path, monkeypatch) -> None:
    """
    Without pruning, undo data is never missing - so this really is corruption.

    Retrying shallower there would start a node on a database Core just said
    was broken, which is the one outcome worse than refusing to start.
    """
    from mining_dark import bitcoin_node

    _conf(tmp_path, "server=1\n", monkeypatch)
    (tmp_path / "debug.log").write_text(_verify_failed_log())

    assert bitcoin_node.pruned_verify_failure() is False


def test_an_unrelated_failure_is_not_mistaken_for_it(tmp_path, monkeypatch) -> None:
    from mining_dark import bitcoin_node

    _conf(tmp_path, "prune=20000\n", monkeypatch)
    (tmp_path / "debug.log").write_text(
        "2026-08-10T18:12:28Z [error] Insufficient dbcache for block verification\n")

    assert bitcoin_node.pruned_verify_failure() is False


def test_a_clean_log_reports_no_failure(tmp_path, monkeypatch) -> None:
    from mining_dark import bitcoin_node

    _conf(tmp_path, "prune=20000\n", monkeypatch)
    (tmp_path / "debug.log").write_text("2026-08-10T17:03:47Z Bitcoin Core version v31.1.0\n")

    assert bitcoin_node.pruned_verify_failure() is False


def test_start_passes_the_check_level_through(tmp_path, monkeypatch) -> None:
    from mining_dark import bitcoin_node

    _conf(tmp_path, "prune=20000\n", monkeypatch)
    monkeypatch.setattr(bitcoin_node, "require_binaries", lambda *_: None)
    monkeypatch.setattr(bitcoin_node, "is_running", lambda: False)
    seen: list = []

    class _Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(bitcoin_node.subprocess, "run",
                        lambda cmd, **_kw: seen.append(cmd) or _Result())

    bitcoin_node.start(check_level=bitcoin_node.SHALLOW_CHECK_LEVEL)

    assert "-checklevel=1" in seen[0]


def test_start_without_a_check_level_passes_no_flag(tmp_path, monkeypatch) -> None:
    from mining_dark import bitcoin_node

    _conf(tmp_path, "prune=20000\n", monkeypatch)
    monkeypatch.setattr(bitcoin_node, "require_binaries", lambda *_: None)
    monkeypatch.setattr(bitcoin_node, "is_running", lambda: False)
    seen: list = []

    class _Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(bitcoin_node.subprocess, "run",
                        lambda cmd, **_kw: seen.append(cmd) or _Result())

    bitcoin_node.start()

    assert not any("checklevel" in arg for arg in seen[0])


def test_an_old_failure_no_longer_counts(tmp_path, monkeypatch) -> None:
    """
    debug.log keeps every failure forever; the fix must be able to clear it.

    Without an age bound the node comes up clean and is still accused of the
    failure it no longer has - and a later, unrelated crash would be retried
    shallower on the strength of a line from hours ago.
    """
    from mining_dark import bitcoin_node

    _conf(tmp_path, "prune=20000\n", monkeypatch)
    (tmp_path / "debug.log").write_text(_verify_failed_log(age_s=6 * 3600))

    assert bitcoin_node.pruned_verify_failure() is False


def test_a_recent_failure_still_counts(tmp_path, monkeypatch) -> None:
    from mining_dark import bitcoin_node

    _conf(tmp_path, "prune=20000\n", monkeypatch)
    (tmp_path / "debug.log").write_text(_verify_failed_log(age_s=10))

    assert bitcoin_node.pruned_verify_failure() is True


def test_a_successful_start_after_the_failure_clears_it(tmp_path, monkeypatch) -> None:
    """The real sequence: it failed, the config was fixed, it now starts fine."""
    from mining_dark import bitcoin_node

    _conf(tmp_path, "prune=20000\n", monkeypatch)
    (tmp_path / "debug.log").write_text(
        _verify_failed_log(age_s=8 * 3600)
        + "2026-08-11T00:00:00Z Bitcoin Core version v31.1.0 (release build)\n"
    )

    assert bitcoin_node.pruned_verify_failure() is False


# ═══════════════════════════════════════════════════════════════════════════════
#  Asking the network, not the node
# ═══════════════════════════════════════════════════════════════════════════════
_TIPS = [
    {"height": 961903, "hash": "0000dead", "branchlen": 6, "status": "invalid"},
    {"height": 961897, "hash": "0000live", "branchlen": 0, "status": "active"},
    {"height": 961633, "hash": "0000head", "branchlen": 2, "status": "headers-only"},
]


def test_invalid_branch_reports_the_lowest_one_above_the_tip(monkeypatch) -> None:
    from mining_dark import bitcoin_node

    monkeypatch.setattr(bitcoin_node, "rpc_call", lambda *a, **k: _TIPS)
    assert bitcoin_node.invalid_branch() == (961903, "0000dead", 6)


def test_a_clean_chain_has_no_invalid_branch(monkeypatch) -> None:
    from mining_dark import bitcoin_node

    clean = [t for t in _TIPS if t["status"] != "invalid"]
    monkeypatch.setattr(bitcoin_node, "rpc_call", lambda *a, **k: clean)
    assert bitcoin_node.invalid_branch() is None


def test_an_invalid_branch_below_the_tip_is_ignored(monkeypatch) -> None:
    """A stale side branch the node already outran is not what blocks a chain."""
    from mining_dark import bitcoin_node

    tips = [
        {"height": 961897, "hash": "0000live", "branchlen": 0, "status": "active"},
        {"height": 900000, "hash": "0000old", "branchlen": 1, "status": "invalid"},
    ]
    monkeypatch.setattr(bitcoin_node, "rpc_call", lambda *a, **k: tips)
    assert bitcoin_node.invalid_branch() is None


def test_invalid_branch_survives_an_unreachable_node(monkeypatch) -> None:
    from mining_dark import bitcoin_node

    monkeypatch.setattr(bitcoin_node, "rpc_call", lambda *a, **k: None)
    assert bitcoin_node.invalid_branch() is None
    assert bitcoin_node.peer_block_height() == 0


def test_peer_height_takes_the_median_not_the_maximum(monkeypatch) -> None:
    """One peer lying high, or one still catching up, must not decide this."""
    from mining_dark import bitcoin_node

    peers = [
        {"synced_headers": 962_745},
        {"synced_headers": 962_745},
        {"synced_headers": 834_220},        # a peer still syncing
        {"synced_headers": 9_999_999},      # a peer talking nonsense
        {"synced_headers": 962_744},
    ]
    monkeypatch.setattr(bitcoin_node, "rpc_call", lambda *a, **k: peers)
    assert bitcoin_node.peer_block_height() == 962_745


def test_peers_with_no_height_are_skipped(monkeypatch) -> None:
    from mining_dark import bitcoin_node

    monkeypatch.setattr(bitcoin_node, "rpc_call",
                        lambda *a, **k: [{"synced_headers": 0}, {}])
    assert bitcoin_node.peer_block_height() == 0
