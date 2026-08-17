"""
The node panel has to refresh itself.

Nothing used to re-probe the node on its own, so the readouts sat frozen at
whatever they held when the dialog opened and the sync progress bar never
moved - the panel looked broken even when the node was healthy.
"""

from __future__ import annotations

import time

import pytest

from mining_dark.gui.panels import settings as settings_panel


class _Runner:
    """Stands in for TaskRunner: records submissions, can report itself busy."""

    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.submitted: list[str] = []

    def submit(self, name: str, fn) -> bool:
        self.submitted.append(name)
        return self.accept


def _dialog(runner: _Runner) -> settings_panel.SettingsDialog:
    dialog = settings_panel.SettingsDialog.__new__(settings_panel.SettingsDialog)
    dialog.tasks = runner
    dialog.bus = object()
    dialog.settings = object()
    dialog._last_node_poll = 0.0
    return dialog


def test_the_first_poll_probes_immediately(monkeypatch) -> None:
    monkeypatch.setattr(settings_panel.time, "monotonic", lambda: 1000.0)
    runner = _Runner()

    _dialog(runner)._poll_node()

    assert runner.submitted == ["node-status"]


def test_polls_are_throttled(monkeypatch) -> None:
    """A probe every frame would hammer the RPC at the render rate."""
    now = 1000.0
    monkeypatch.setattr(settings_panel.time, "monotonic", lambda: now)
    runner = _Runner()
    dialog = _dialog(runner)

    dialog._poll_node()
    for _ in range(200):                     # many frames, same instant
        dialog._poll_node()

    assert runner.submitted == ["node-status"]


def test_the_next_probe_comes_after_the_period(monkeypatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr(settings_panel.time, "monotonic", lambda: clock["now"])
    runner = _Runner()
    dialog = _dialog(runner)

    dialog._poll_node()
    clock["now"] += settings_panel._NODE_POLL_PERIOD + 0.01
    dialog._poll_node()

    assert runner.submitted == ["node-status", "node-status"]


def test_a_busy_runner_does_not_stack_up_probes(monkeypatch) -> None:
    """While a rebuild holds the runner, declined probes must not queue."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(settings_panel.time, "monotonic", lambda: clock["now"])
    runner = _Runner(accept=False)
    dialog = _dialog(runner)

    for _ in range(5):
        dialog._poll_node()
        clock["now"] += settings_panel._NODE_POLL_PERIOD + 0.01

    # One attempt per period, never more - and none of them raised.
    assert runner.submitted == ["node-status"] * 5


# ═══════════════════════════════════════════════════════════════════════════════
#  The snapshot export checkbox
#
#  It is only offered when pressing REBUILD would actually be accepted, so the
#  panel mirrors what utxo_updater._announce_snapshot_export enforces.
# ═══════════════════════════════════════════════════════════════════════════════
class _Node:
    def __init__(self, running: bool) -> None:
        self.running = running


def _snapshot_dialog():
    """The check caches its answer, so it needs a real instance to cache on."""
    dialog = settings_panel.SettingsDialog.__new__(settings_panel.SettingsDialog)
    dialog._last_snapshot_check = 0.0
    dialog._snapshot_dir_state = "none"
    return dialog


def test_snapshot_export_offered_when_loaded_and_node_stopped(monkeypatch) -> None:
    from mining_dark import bitcoin_node

    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "loaded")

    assert _snapshot_dialog()._snapshot_exportable(_Node(running=False))


def test_snapshot_export_hidden_while_the_node_runs(monkeypatch) -> None:
    """A live node can leave the on-disk chainstate behind the tip."""
    from mining_dark import bitcoin_node

    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "loaded")

    assert not _snapshot_dialog()._snapshot_exportable(_Node(running=True))


def test_snapshot_export_hidden_without_a_validated_snapshot(monkeypatch) -> None:
    from mining_dark import bitcoin_node

    for state in ("none", "orphaned", "loading"):
        monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda s=state: s)
        assert not _snapshot_dialog()._snapshot_exportable(_Node(running=False))


def test_the_export_check_does_not_disarm_a_pending_load(monkeypatch) -> None:
    """
    It runs every frame, right after the arm is set.

    A copy of the constructor's field resets had been pasted into this method,
    so with the node stopped - exactly the state an armed load waits in - the
    deadline was zeroed a few seconds after the click and the load never fired.
    """
    import time as _time

    from mining_dark import bitcoin_node

    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "none")
    dialog = _snapshot_dialog()
    deadline = _time.monotonic() + settings_panel._ARM_TIMEOUT
    dialog._load_when_ready = deadline
    dialog._snapshot_step = "load"

    for _ in range(3):
        dialog._last_snapshot_check = 0.0        # force past the cache
        dialog._snapshot_exportable(_Node(running=False))

    assert dialog._load_when_ready == deadline
    assert dialog._snapshot_step == "load"


# ═══════════════════════════════════════════════════════════════════════════════
#  Rebuild progress
#
#  A rebuild runs for tens of minutes while the TaskRunner is held by it, so the
#  panel reads the temp files directly - anything asking the runner would report
#  nothing for exactly as long as the progress mattered.
# ═══════════════════════════════════════════════════════════════════════════════
def test_bytes_are_shown_in_a_unit_that_says_something() -> None:
    """"0.0 GB" for 20 MB is worse than no number at all."""
    assert settings_panel._human_bytes(18_400_000_000) == "18.40 GB"
    assert settings_panel._human_bytes(20_000_000) == "20 MB"
    assert settings_panel._human_bytes(127_000) == "127 KB"


def test_elapsed_time_reads_as_a_clock() -> None:
    assert settings_panel._hms(9) == "0:09"
    assert settings_panel._hms(125) == "2:05"
    assert settings_panel._hms(3_725) == "1:02:05"


class _Disk:
    estimated_csv_bytes = 20_000_000
    estimated_db_bytes = 10_000_000


def _progress_dialog(tmp_path, monkeypatch):
    from mining_dark import paths

    monkeypatch.setattr(paths, "UTXO_TMP_CSV", tmp_path / "utxo_dump_tmp.csv")

    class _Utxo:
        @staticmethod
        def resolved_db_file():
            return tmp_path / "utxo.db"

    class _Settings:
        utxo = _Utxo()

    dialog = settings_panel.SettingsDialog.__new__(settings_panel.SettingsDialog)
    dialog.settings = _Settings()
    # A rebuild in flight, started a moment ago: the panel only counts temp
    # files this run has written, so there has to be a run.
    dialog._rebuild_started = settings_panel.time.monotonic() - 1.0
    return dialog


def test_no_progress_before_the_export_writes_anything(tmp_path, monkeypatch) -> None:
    assert _progress_dialog(tmp_path, monkeypatch)._rebuild_progress(_Disk()) is None


def test_the_export_phase_tracks_the_csv(tmp_path, monkeypatch) -> None:
    dialog = _progress_dialog(tmp_path, monkeypatch)
    (tmp_path / "utxo_dump_tmp.csv").write_bytes(b"x" * 5_000_000)

    fraction, phase, done, expected = dialog._rebuild_progress(_Disk())

    assert phase.endswith("phase_export")
    assert done == 5_000_000
    assert fraction == pytest.approx(0.25)


def test_the_import_phase_takes_over_from_the_csv(tmp_path, monkeypatch) -> None:
    """Once the database is being built the CSV size says nothing about progress."""
    dialog = _progress_dialog(tmp_path, monkeypatch)
    (tmp_path / "utxo_dump_tmp.csv").write_bytes(b"x" * 20_000_000)
    (tmp_path / "utxo.tmp.db").write_bytes(b"x" * 2_000_000)

    fraction, phase, done, _ = dialog._rebuild_progress(_Disk())

    assert phase.endswith("phase_import")
    assert done == 2_000_000
    assert fraction == pytest.approx(0.2)


def test_progress_never_overflows_the_bar(tmp_path, monkeypatch) -> None:
    """The expected sizes are an order of magnitude, not a promise."""
    dialog = _progress_dialog(tmp_path, monkeypatch)
    (tmp_path / "utxo_dump_tmp.csv").write_bytes(b"x" * 90_000_000)

    fraction, _, _, _ = dialog._rebuild_progress(_Disk())

    assert fraction == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
#  The progress bar reads files a rebuild is renaming underneath it
# ═══════════════════════════════════════════════════════════════════════════════
def test_a_vanished_file_reads_as_zero_not_an_exception(tmp_path) -> None:
    """
    exists()-then-stat() raised FileNotFoundError when finalize() renamed the
    temp database away mid-frame, and the exception unwound out of the render
    loop and destroyed the window.
    """
    assert settings_panel._size_or_zero(tmp_path / "nunca_existiu") == 0

    real = tmp_path / "algo"
    real.write_bytes(b"x" * 4096)
    assert settings_panel._size_or_zero(real) == 4096


def test_progress_survives_files_disappearing(tmp_path, monkeypatch) -> None:
    """The whole point: no exception escapes toward the render loop."""
    from mining_dark import paths

    csv_path = tmp_path / "utxo_dump_tmp.csv"
    monkeypatch.setattr(paths, "UTXO_TMP_CSV", csv_path)

    class _Vanishing(type(tmp_path)):
        pass

    class _Utxo:
        @staticmethod
        def resolved_db_file():
            return tmp_path / "utxo.db"

    class _Settings:
        utxo = _Utxo()

    dialog = settings_panel.SettingsDialog.__new__(settings_panel.SettingsDialog)
    dialog.settings = _Settings()
    dialog._rebuild_started = settings_panel.time.monotonic() - 1.0

    class _Disk:
        estimated_csv_bytes = 1000
        estimated_db_bytes = 1000

    # Nothing on disk at all - the state right after the CSV is unlinked.
    assert dialog._rebuild_progress(_Disk()) is None


# ═══════════════════════════════════════════════════════════════════════════════
#  SAVE must not rewrite a value the user never touched
# ═══════════════════════════════════════════════════════════════════════════════
def test_the_worker_slider_covers_every_valid_value() -> None:
    """
    A slider narrower than the model's range fed its own clamp back out through
    collect(), so opening Settings to change the theme knocked workers from 300
    down to 128 with nothing said.
    """
    from mining_dark.config.settings import ScannerConfig

    settings = ScannerConfig()
    settings.workers = 512                      # the model's maximum
    assert settings_panel._MAX_WORKERS_UI >= settings.workers


# ═══════════════════════════════════════════════════════════════════════════════
#  Two bars, because the phases never advance together
# ═══════════════════════════════════════════════════════════════════════════════
def test_the_header_phase_ends_on_blocks_not_on_progress() -> None:
    """
    verificationprogress is not zero while headers download - it reports values
    like 7e-10 - so using it to decide parked the header bar at 100% with the
    headers still arriving.  `blocks` is the honest signal.
    """
    class _Node:
        blocks = 0
        progress = 7.18e-10          # measured on a real node mid-header-sync
        header_progress = 0.30

    assert _Node.blocks == 0, "sanity"
    headers_done = _Node.blocks > 0
    assert not headers_done
    assert (1.0 if headers_done else _Node.header_progress) == 0.30

    class _Syncing(_Node):
        blocks = 100

    headers_done = _Syncing.blocks > 0
    assert headers_done
    assert (1.0 if headers_done else _Syncing.header_progress) == 1.0


# ═══════════════════════════════════════════════════════════════════════════════
#  The dialog has to grow with the font
#
#  Every widget inside scales with the text, so a fixed modal pushed the node
#  tab's last controls past the bottom edge: measured content down to y=723 in
#  a 700px window at scale 2.0, which the slider allows.
# ═══════════════════════════════════════════════════════════════════════════════
def _dialog_at_scale(scale: float):
    from mining_dark.gui.panels.common import PanelContext
    from mining_dark.gui.theme import Fonts

    dialog = settings_panel.SettingsDialog.__new__(settings_panel.SettingsDialog)
    dialog.ctx = PanelContext(palette=None, themes=None, fonts=Fonts(scale=scale))
    return dialog


def test_the_modal_follows_the_font_scale() -> None:
    base_w, base_h = _dialog_at_scale(1.0)._modal_size

    assert (base_w, base_h) == (settings_panel._MODAL_W, settings_panel._MODAL_H)

    big_w, big_h = _dialog_at_scale(2.0)._modal_size
    assert big_w == base_w * 2
    assert big_h == base_h * 2

    # Deliberately never smaller: spacers and separators are fixed pixels and
    # do not shrink with the font, so a modal scaled down lost height against
    # content that had barely moved, and a tab that fitted started scrolling.
    small_w, small_h = _dialog_at_scale(0.7)._modal_size
    assert (small_w, small_h) == (base_w, base_h)


def test_a_missing_scale_falls_back_to_one() -> None:
    """Fonts predating the field, or a headless run with no font file."""
    from mining_dark.gui.panels.common import PanelContext
    from mining_dark.gui.theme import Fonts

    dialog = settings_panel.SettingsDialog.__new__(settings_panel.SettingsDialog)
    dialog.ctx = PanelContext(palette=None, themes=None, fonts=Fonts(scale=0.0))

    assert dialog._modal_size == (settings_panel._MODAL_W, settings_panel._MODAL_H)


# ═══════════════════════════════════════════════════════════════════════════════
#  Sizes authored at scale 1.0 must follow the font
# ═══════════════════════════════════════════════════════════════════════════════
def test_px_scales_sizes_with_the_font() -> None:
    """
    Widget sizes are fixed pixels while the text inside them is not, so at scale
    2.0 labels outgrew their buttons: "WALLETS ENCONTRADAS" rendered as
    "WALLETS ENCONTRADA" and the footer buttons overlapped each other.
    """
    from mining_dark.gui.panels.common import PanelContext
    from mining_dark.gui.theme import Fonts

    def ctx(scale):
        return PanelContext(palette=None, themes=None, fonts=Fonts(scale=scale))

    assert ctx(1.0).px(104) == 104
    assert ctx(2.0).px(104) == 208
    assert ctx(0.7).px(104) == 72
    # A Fonts with no usable scale must not collapse every widget to zero.
    assert ctx(0.0).px(104) == 104


def test_the_header_and_footer_reserve_room_for_their_text() -> None:
    """Two stacked rows live in the header; a fixed height made them overlap."""
    from mining_dark.gui.panels.common import PanelContext
    from mining_dark.gui.panels.footer import FooterPanel
    from mining_dark.gui.panels.header import HeaderPanel
    from mining_dark.gui.theme import Fonts

    big = PanelContext(palette=None, themes=None, fonts=Fonts(scale=2.0))

    assert HeaderPanel.height(big) == HeaderPanel.HEIGHT * 2
    assert FooterPanel.height(big) == FooterPanel.HEIGHT * 2


# ═══════════════════════════════════════════════════════════════════════════════
#  The snapshot row offers one action, and never one that would fail
# ═══════════════════════════════════════════════════════════════════════════════
def _step(**kwargs):
    base = dict(already=False, downloading=False, complete=False,
                busy=False, node_running=False, headers=0)
    base.update(kwargs)
    return settings_panel.snapshot_next_step(**base)


def test_nothing_downloaded_offers_the_download() -> None:
    step, enabled, reason = _step()
    assert (step, enabled, reason) == ("download", True, "")


def test_a_partial_file_still_offers_the_download() -> None:
    """Resume, not restart - the button reads the same because it does the same."""
    assert _step(complete=False)[:2] == ("download", True)


def test_a_running_download_offers_to_stop_it() -> None:
    assert _step(downloading=True)[:2] == ("cancel", True)


def test_a_stopped_node_is_offered_start_and_load() -> None:
    """
    loadtxoutset is an RPC call, so with bitcoind down the click cannot work -
    but refusing it just made the user go do it by hand.  The button offers to
    do both instead, and says so in its label.
    """
    assert _step(complete=True, node_running=False)[:2] == ("start_and_load", True)


def test_an_armed_load_reports_progress_instead_of_a_refusal() -> None:
    step, enabled, reason = _step(complete=True, node_running=True,
                                  headers=226_782, armed=True)
    assert (step, enabled) == ("load", False)
    assert "226" in reason and "935" in reason


def test_an_armed_load_says_so_while_the_node_starts() -> None:
    step, enabled, reason = _step(complete=True, node_running=False, armed=True)
    assert (step, enabled) == ("load", False)
    assert reason


def test_loading_waits_for_the_header_chain() -> None:
    """
    Core anchors a snapshot to a header it already has.  Asking too early fails
    with an opaque RPC error that reads like a corrupt file.
    """
    step, enabled, reason = _step(complete=True, node_running=True, headers=226_782)
    assert (step, enabled) == ("load", False)
    assert "226" in reason and "935" in reason


def test_loading_is_offered_once_the_headers_arrive() -> None:
    assert _step(complete=True, node_running=True,
                 headers=settings_panel._SNAPSHOT_LOAD_HEIGHT)[:2] == ("load", True)


def test_an_already_loaded_snapshot_offers_nothing() -> None:
    """One loadtxoutset per datadir - the row has nothing left to do."""
    assert _step(already=True, complete=True, node_running=True,
                 headers=999_999)[:2] == ("none", False)


def test_another_task_holds_the_button() -> None:
    assert _step(busy=True)[1] is False
    assert _step(complete=True, node_running=True, headers=999_999, busy=True)[1] is False


# ═══════════════════════════════════════════════════════════════════════════════
#  Start and stop are one button
#
#  They are never both applicable, so offering both left one permanently greyed
#  out beside the other - the clutter the snapshot row already shed.
# ═══════════════════════════════════════════════════════════════════════════════
def _power(**kwargs):
    base = dict(available=True, running=False, task="", detail="")
    base.update(kwargs)
    return settings_panel.node_power_step(**base)


def test_a_stopped_node_offers_start() -> None:
    assert _power()[:2] == ("start", True)


def test_a_running_node_offers_stop() -> None:
    assert _power(running=True)[:2] == ("stop", True)


def test_a_start_in_flight_is_not_clickable_again() -> None:
    """Clicking twice only starts a second doomed bitcoind."""
    step, enabled, reason = _power(task="node-start")
    assert (step, enabled) == ("start", False)
    assert reason


def test_a_stop_in_flight_keeps_showing_stop() -> None:
    step, enabled, reason = _power(running=True, task="node-stop")
    assert (step, enabled) == ("stop", False)
    assert reason


def test_another_task_blocks_the_node_button() -> None:
    assert _power(task="utxo-rebuild")[1] is False


def test_missing_binaries_explain_themselves() -> None:
    step, enabled, reason = _power(available=False, detail="bitcoind nao encontrado")
    assert (step, enabled) == ("start", False)
    assert "bitcoind" in reason


# ═══════════════════════════════════════════════════════════════════════════════
#  Step 3 is gated on step 2
# ═══════════════════════════════════════════════════════════════════════════════
#  The pipeline refuses each of these too, but only after the click, from a
#  worker thread, into the terminal.  These assert the panel decides first.

def _rebuild(**kwargs):
    base = dict(
        busy=False, scanning=False, available=True, running=True,
        reachable=True, ibd=False, blocks=935_100, headers=935_100, progress=1.0,
        from_snapshot=False, snapshot_exportable=False,
    )
    base.update(kwargs)
    return settings_panel.utxo_rebuild_step(**base)


def test_a_synced_node_can_be_dumped() -> None:
    assert _rebuild() == (True, "")


def test_a_stopped_node_blocks_the_rebuild() -> None:
    enabled, reason = _rebuild(running=False)
    assert enabled is False
    assert reason


def test_a_node_behind_the_tip_blocks_the_rebuild() -> None:
    """A node hours behind still reports 0.9999+; blocks == headers is the check."""
    enabled, reason = _rebuild(blocks=930_000, headers=935_100)
    assert enabled is False
    assert "5.100" in reason or "5,100" in reason


def test_initial_block_download_blocks_the_rebuild() -> None:
    assert _rebuild(ibd=True)[0] is False


def test_unfinished_verification_blocks_the_rebuild() -> None:
    assert _rebuild(progress=0.98)[0] is False


def test_missing_binaries_block_the_rebuild() -> None:
    enabled, reason = _rebuild(available=False, running=False)
    assert enabled is False
    assert reason


def test_the_offline_export_ignores_the_sync_checks() -> None:
    """chainstate_snapshot/ is complete at its height by construction."""
    assert _rebuild(running=False, from_snapshot=True,
                    snapshot_exportable=True) == (True, "")


def test_the_offline_export_still_needs_a_loaded_snapshot() -> None:
    assert _rebuild(running=False, from_snapshot=True,
                    snapshot_exportable=False)[0] is False


def test_a_running_task_blocks_the_rebuild() -> None:
    assert _rebuild(busy=True)[0] is False


def test_a_running_scan_blocks_the_rebuild() -> None:
    assert _rebuild(scanning=True)[0] is False


def test_gate_matches_pipeline_threshold() -> None:
    """The panel copies the constant rather than importing the pipeline."""
    from mining_dark import utxo_updater

    assert settings_panel._MIN_VERIFICATION_PROGRESS == \
        utxo_updater._MIN_VERIFICATION_PROGRESS


# ═══════════════════════════════════════════════════════════════════════════════
#  loadtxoutset progress, read off debug.log
# ═══════════════════════════════════════════════════════════════════════════════
#  The RPC that starts the load holds cs_main for its whole duration, so
#  getblockchaininfo blocks and the log is the only thing still answering.
#  Sample lines are copied verbatim from a real 164-million-coin load.

_LOAD_START = (
    "2026-08-09T21:31:27Z [snapshot] loading 164241311 coins from snapshot "
    "0000000000000000000147034958af1652b2b91bba607beacc5e72a56f0fb5ee")
_LOAD_TICK = "2026-08-09T21:33:52Z [snapshot] 22000000 coins loaded (13.39%, 490 MB)"
_LOAD_DONE = (
    "2026-08-09T21:57:13Z [snapshot] loaded 164241311 (78 MB) coins from snapshot "
    "0000000000000000000147034958af1652b2b91bba607beacc5e72a56f0fb5ee")


def _progress(tail: str, monkeypatch):
    from mining_dark import bitcoin_node

    monkeypatch.setattr(bitcoin_node, "_log_tail", lambda _bytes: tail)
    return bitcoin_node.snapshot_load_progress()


def test_a_log_without_a_load_reports_nothing(monkeypatch) -> None:
    assert _progress("2026-08-09T21:31:21Z Bitcoin Core version v31.1.0\n",
                     monkeypatch) is None


def test_a_started_load_reports_zero_not_nothing(monkeypatch) -> None:
    """Between the click and Core's first tick the bar still has to say something."""
    fraction, done, total = _progress(_LOAD_START, monkeypatch)

    assert (fraction, done, total) == (0.0, 0, 164_241_311)


def test_a_tick_carries_the_percentage(monkeypatch) -> None:
    fraction, done, _ = _progress("\n".join([_LOAD_START, _LOAD_TICK]), monkeypatch)

    assert done == 22_000_000
    assert fraction == pytest.approx(0.1339)


def test_the_total_is_derived_when_the_start_line_is_gone(monkeypatch) -> None:
    """
    The line naming the total is written once, hours before the end.

    Any tail worth reading has long since scrolled past it, so the total comes
    from the tick itself - done over percent.  Core rounds that percentage to
    two decimals, which caps the derived total at about 0.04% - far inside what
    a progress bar can show.
    """
    _, _, total = _progress(_LOAD_TICK, monkeypatch)

    assert total == pytest.approx(164_241_311, rel=5e-4)


def test_a_finished_load_reports_full(monkeypatch) -> None:
    fraction, done, total = _progress("\n".join([_LOAD_START, _LOAD_TICK, _LOAD_DONE]),
                                      monkeypatch)

    assert (fraction, done, total) == (1.0, 164_241_311, 164_241_311)


def test_a_new_load_is_not_read_as_the_previous_one_finishing(monkeypatch) -> None:
    """
    A second load starts with the first one's completion line still in the tail.

    Taking the last event of any kind - rather than the last tick, or any
    completion anywhere - is what keeps the bar from opening at 100%.
    """
    tail = "\n".join([_LOAD_TICK, _LOAD_DONE, _LOAD_START])

    assert _progress(tail, monkeypatch) == (0.0, 0, 164_241_311)


def test_an_unreadable_log_reports_nothing(monkeypatch) -> None:
    assert _progress("", monkeypatch) is None


def test_a_half_loaded_snapshot_blocks_the_rebuild() -> None:
    """
    A node up at the tip is not enough on its own.

    `require_dumpable_chainstate` refuses both in-between states, because
    exporting from a chainstate_snapshot/ without base_blockhash yields a
    truncated UTXO set that nothing downstream can tell from a correct one.
    """
    for state in ("loading", "orphaned"):
        enabled, reason = _rebuild(chainstate=state)
        assert enabled is False, state
        assert reason, state


def test_a_datadir_that_never_had_a_snapshot_is_fine() -> None:
    """'none' is a normal IBD node - the case collapsing states used to break."""
    assert _rebuild(chainstate="none") == (True, "")


def test_a_loaded_snapshot_is_fine() -> None:
    assert _rebuild(chainstate="loaded") == (True, "")


def test_the_offline_export_is_unaffected_by_the_chainstate_gate() -> None:
    """It reads chainstate_snapshot/ on purpose; snapshot_exportable covers it."""
    assert _rebuild(running=False, from_snapshot=True, snapshot_exportable=True,
                    chainstate="loaded") == (True, "")


def test_the_state_cache_keeps_the_whole_string(monkeypatch) -> None:
    from mining_dark import bitcoin_node

    for state in ("none", "loading", "orphaned", "loaded"):
        monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda s=state: s)
        dialog = _snapshot_dialog()
        assert dialog._snapshot_state() == state
        assert dialog._snapshot_loaded() is (state == "loaded")


def test_a_node_that_has_not_answered_rpc_says_so() -> None:
    """
    A probe taken while the RPC was busy reports every field as zero.

    Those zeros walk past the sync checks - `headers` is 0, so nothing is
    "short of the tip" - and used to land on "verification has not finished",
    which describes nothing that is actually happening.
    """
    enabled, reason = _rebuild(reachable=False, ibd=False, blocks=0, headers=0,
                               progress=0.0)

    assert enabled is False
    assert reason == settings_panel.t("settings.utxo.needs_rpc")


# ═══════════════════════════════════════════════════════════════════════════════
#  The panel's own probe must not disable the panel
# ═══════════════════════════════════════════════════════════════════════════════
def test_the_status_probe_does_not_hold_the_buttons() -> None:
    """
    `node-status` is a read-only RPC round trip this panel fires every 3s.

    Treating it as a busy runner greyed STOP NODE out for the length of each
    call and let it back in between - a red button blinking to disabled and
    back at the poll rate, worst on a syncing node where the RPC is slowest.
    """
    assert settings_panel.blocking_task("node-status") == ""


def test_real_jobs_still_hold_the_buttons() -> None:
    for task in ("node-start", "node-stop", "utxo-rebuild",
                 "snapshot-get", "snapshot-load"):
        assert settings_panel.blocking_task(task) == task


def test_stop_stays_clickable_across_a_status_probe() -> None:
    """The button must not change appearance while a probe is in flight."""
    idle = _power(running=True, task=settings_panel.blocking_task(""))
    probing = _power(running=True, task=settings_panel.blocking_task("node-status"))

    assert idle == probing == ("stop", True, "")


def test_the_rebuild_gate_ignores_the_status_probe() -> None:
    assert _rebuild(busy=bool(settings_panel.blocking_task("node-status"))) == (True, "")


# ═══════════════════════════════════════════════════════════════════════════════
#  Leftovers from an interrupted rebuild are not progress
# ═══════════════════════════════════════════════════════════════════════════════
#  Both temp files survive a rebuild that is killed outright: the cleanup runs
#  in a `finally`, and nothing runs after SIGKILL, a closed terminal or a power
#  cut.  Read at face value they made the *next* rebuild open on the import
#  phase, frozen at the byte count the dead one reached - the file is not
#  touched during export, so the bar sat there for the whole of it.

def test_a_stale_temp_file_is_not_counted(tmp_path, monkeypatch) -> None:
    import os

    from mining_dark import paths

    csv_path = tmp_path / "utxo_dump_tmp.csv"
    monkeypatch.setattr(paths, "UTXO_TMP_CSV", csv_path)
    dialog = _progress_dialog(tmp_path, monkeypatch)

    # Left behind by a run that died an hour ago.
    (tmp_path / "utxo.tmp.db").write_bytes(b"x" * 125_000_000)
    old = time.time() - 3600
    os.utime(tmp_path / "utxo.tmp.db", (old, old))

    assert dialog._rebuild_progress(_Disk()) is None


def test_a_stale_file_does_not_hide_the_live_one(tmp_path, monkeypatch) -> None:
    """
    The exact shape of the bug: a leftover .tmp.db beside a growing CSV.

    The import phase is checked first, so the stale database won and the export
    that was actually running never got reported.
    """
    import os

    from mining_dark import paths

    csv_path = tmp_path / "utxo_dump_tmp.csv"
    monkeypatch.setattr(paths, "UTXO_TMP_CSV", csv_path)
    dialog = _progress_dialog(tmp_path, monkeypatch)

    (tmp_path / "utxo.tmp.db").write_bytes(b"x" * 125_000_000)
    old = time.time() - 3600
    os.utime(tmp_path / "utxo.tmp.db", (old, old))
    csv_path.write_bytes(b"x" * 5_000_000)          # this run, right now

    fraction, phase, done, _ = dialog._rebuild_progress(_Disk())

    assert phase.endswith("phase_export")
    assert done == 5_000_000


def test_a_fresh_temp_file_is_counted(tmp_path, monkeypatch) -> None:
    dialog = _progress_dialog(tmp_path, monkeypatch)
    (tmp_path / "utxo.tmp.db").write_bytes(b"x" * 4_000_000)

    fraction, phase, done, _ = dialog._rebuild_progress(_Disk())

    assert phase.endswith("phase_import")
    assert done == 4_000_000


def test_nothing_is_counted_before_a_rebuild_starts(tmp_path, monkeypatch) -> None:
    """No run in flight means no file on disk can be this run's output."""
    dialog = _progress_dialog(tmp_path, monkeypatch)
    dialog._rebuild_started = None
    (tmp_path / "utxo.tmp.db").write_bytes(b"x" * 4_000_000)

    assert dialog._rebuild_progress(_Disk()) is None



def test_stall_threshold_matches_doctor() -> None:
    """
    The panel duplicates the constant rather than importing it, so opening the
    dialog does not drag the doctor in.  This is what keeps the two honest.
    """
    from mining_dark import doctor
    from mining_dark.gui.panels import settings as settings_panel

    assert settings_panel._MAX_BLOCKS_BEHIND == doctor.MAX_BLOCKS_BEHIND


def test_the_panel_only_calls_a_node_behind_when_it_stopped_downloading() -> None:
    """
    A node in IBD is behind by design and the progress bar says so; flagging it
    would cry wolf for the hours a sync legitimately takes.
    """
    from mining_dark.gui.panels.settings import SettingsDialog
    from mining_dark.gui.state import NodeEvent

    behind = SettingsDialog._behind_network

    assert behind(NodeEvent(blocks=961_897, peer_height=962_745, ibd=False))
    assert not behind(NodeEvent(blocks=936_579, peer_height=962_745, ibd=True))
    assert not behind(NodeEvent(blocks=962_744, peer_height=962_745, ibd=False))
    # Nothing to compare against yet - silence, not a warning.
    assert not behind(NodeEvent(blocks=961_897, peer_height=0, ibd=False))
    assert not behind(NodeEvent(blocks=0, peer_height=962_745, ibd=False))
