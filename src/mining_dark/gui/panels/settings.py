"""
Settings dialog - the gear button in the header.

Four tabs cover everything the project needs to be told: what to scan, where to
put the results, how to drive Bitcoin Core, and how the interface should look.
Values are read out of `Settings` when the dialog opens and written back only
when SAVE is pressed, so a half-finished edit never leaks into a running scan.

Long operations (starting bitcoind, rebuilding the UTXO database) are handed to
the `TaskRunner`; this module never blocks the render loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import dearpygui.dearpygui as dpg

from mining_dark.gui import services
from mining_dark.i18n import LANGUAGES, get_language, t
from mining_dark.gui.panels.common import (
    MODAL_FOOTER_H,
    PanelContext,
    fit_and_center,
    group_thousands,
    set_text,
    styled_text,
)
from mining_dark.gui.state import EventBus, RunState, UIState
from mining_dark.gui.theme import PALETTES

_MODAL = "settings_modal"
_STATUS = "settings_status"

#: Modal size at font scale 1.0.  Tall enough that the node tab - the longest
#: one, and the only one that ever needed a scrollbar - fits whole: its content
#: measures 662px with the footer, and the old 700 left nothing spare once the
#: snapshot row arrived.  fit_and_center caps this to the window height anyway
#: (viewport - 80), so asking for more simply uses whatever the screen has.
_MODAL_W = 940
_MODAL_H = 860

_ADDRESS_TYPES = (
    ("p2pkh", "P2PKH  ·  legacy  1..."),
    ("p2pkh_uncompressed", "P2PKH uncompressed  ·  1...  (era Satoshi)"),
    ("p2sh_p2wpkh", "P2SH-P2WPKH  ·  nested segwit  3..."),
    ("p2wpkh", "P2WPKH  ·  native segwit  bc1q..."),
    ("p2wsh", "P2WSH  ·  script hash  bc1q..."),
    ("p2tr", "P2TR  ·  taproot  bc1p..."),
)

# Matches ScannerConfig._validate_workers.  A narrower slider silently rewrote
# any larger value on SAVE - opening Settings to change the theme was enough to
# knock workers from 300 down to 128, with nothing said.
_MAX_WORKERS_UI = 512

# How often the open dialog re-probes the node.  Fast enough that a syncing
# node visibly advances, slow enough that the RPC is not hammered.
_NODE_POLL_PERIOD = 3.0

# How often the rebuild bar re-stats the temp files.  Two syscalls, so this
# is about not repainting text 60 times a second rather than about cost.
_REBUILD_POLL_PERIOD = 0.5

# How long a snapshot-state answer stays good for.  It only changes when a
# node starts, stops or finishes loading a snapshot.
_SNAPSHOT_STATE_TTL = 3.0

# Both sync bars share this so neither reads as more precise than the other.
# Two decimals: enough that early block verification is not stuck on 0.0%,
# without the noise of trailing digits once it approaches the tip.  The
# block counter beside the bar carries the fine-grained movement anyway.
_PCT_FMT = ".2f"

#: Size of the assumeutxo snapshot, so the bar has a target without a network
#: round trip on every frame.  Only drives the display - the real check before
#: loading asks the server for Content-Length.
_SNAPSHOT_EXPECTED_BYTES = 9_387_990_306

#: The height the snapshot anchors to.  loadtxoutset needs the header chain
#: to have reached it first.
_SNAPSHOT_LOAD_HEIGHT = 935_000

#: Mirror of `utxo_updater._MIN_VERIFICATION_PROGRESS`, duplicated rather than
#: imported so opening the settings dialog does not drag in the whole export
#: pipeline.  `test_gate_matches_pipeline_threshold` fails if the two drift.
_MIN_VERIFICATION_PROGRESS = 0.9999

#: Mirror of `doctor.MAX_BLOCKS_BEHIND`, duplicated rather than imported for
#: the same reason as the constant above.  `test_stall_threshold_matches_doctor`
#: fails if the two drift.
_MAX_BLOCKS_BEHIND = 6

#: How long an armed load waits for the node before giving up.  Header sync
#: runs for tens of minutes; past this the plan is stale enough that firing it
#: would surprise whoever walked away from the machine.
_ARM_TIMEOUT = 60 * 60.0

#: The single button's label per step of the job.
_SNAPSHOT_BUTTON_LABELS = {
    "download": "settings.node.snapshot_download",
    "cancel": "settings.node.snapshot_cancel",
    "load": "settings.node.snapshot_load",
    "start_and_load": "settings.node.snapshot_start_and_load",
    "none": "settings.node.snapshot_download",
}


def snapshot_next_step(
    *,
    already: bool,
    downloading: bool,
    complete: bool,
    busy: bool,
    node_running: bool,
    headers: int,
    armed: bool = False,
) -> tuple:
    """
    The one action the snapshot row currently offers: (step, enabled, reason).

    Loading needs bitcoind up and its header chain past the snapshot height, so
    rather than refusing the click and telling the user to go do that, the
    button offers to do it: start the node and arm the load, which fires by
    itself once the headers arrive.  Header sync runs for tens of minutes, so
    chaining it inside one blocking job would hold the TaskRunner - and STOP
    with it - for the whole wait; arming keeps each job short.

    Kept free of Dear PyGui so the decision table can be tested without a window.
    """
    if already:
        # Core allows one loadtxoutset per datadir; there is nothing left to do.
        return "none", False, ""
    if downloading:
        return "cancel", True, ""
    if not complete:
        return "download", not busy, ""

    if armed:
        # Already on its way: the node is starting or the headers are coming in.
        if not node_running:
            return "load", False, t("settings.node.snapshot_armed_starting")
        return "load", False, t(
            "settings.node.snapshot_armed_headers",
            have=group_thousands(headers),
            need=group_thousands(_SNAPSHOT_LOAD_HEIGHT),
        )

    if not node_running:
        return "start_and_load", not busy, ""
    if headers < _SNAPSHOT_LOAD_HEIGHT:
        # Core can only anchor a snapshot to a header it already knows; asking
        # earlier fails with an opaque RPC error that reads like a corrupt file
        # and sends people hunting the wrong problem.
        return "load", False, t(
            "settings.node.snapshot_needs_headers",
            have=group_thousands(headers),
            need=group_thousands(_SNAPSHOT_LOAD_HEIGHT),
        )
    return "load", not busy, ""


#: Jobs the panel runs for itself, which no button needs to wait behind.  The
#: node probe is a read-only RPC round trip fired every few seconds while this
#: dialog is open; counting it as "busy" greyed every control out for the
#: length of each call, so STOP NODE blinked between red and disabled at the
#: poll rate - on a syncing node, where the RPC is slowest, most of the time.
_SELF_TASKS = ("node-status",)


def blocking_task(task: str) -> str:
    """The task name if it should hold the buttons, "" if it is the panel's own."""
    return "" if task in _SELF_TASKS else task


def node_power_step(
    *,
    available: bool,
    running: bool,
    task: str,
    detail: str,
) -> tuple:
    """
    The one node action currently on offer: (step, enabled, reason).

    Start and stop are never both applicable, so showing both left one of them
    permanently greyed out next to the other - the same clutter the snapshot
    row had before it collapsed to a single button.
    """
    if not available:
        return "start", False, detail or t("settings.node.binaries_missing")
    if task == "node-start":
        return "start", False, t("settings.node.wait_note")
    if task == "node-stop":
        return "stop", False, t("settings.node.wait_note")
    if running:
        return "stop", not task, ""
    return "start", not task, ""


def utxo_rebuild_step(
    *,
    busy: bool,
    scanning: bool,
    available: bool,
    running: bool,
    reachable: bool,
    ibd: bool,
    blocks: int,
    headers: int,
    progress: float,
    from_snapshot: bool,
    snapshot_exportable: bool,
    chainstate: str = "none",
) -> tuple:
    """
    Whether a rebuild would be accepted right now: (enabled, reason).

    The three sections of this tab are the three steps of one job, and step 3
    cannot run until step 2 has actually produced a chainstate at the tip.
    `utxo_updater` already refuses every case below, but it does so after the
    click, in the terminal, by raising SystemExit out of a worker thread - so
    the panel would sit there looking like a rebuild was under way.  Deciding
    it here turns each refusal into a greyed-out button that says what is
    missing, and the pipeline's own checks stay as the backstop.

    Mirrors `utxo_updater._sync_shortfall`; kept free of Dear PyGui so the
    table can be tested without a window.
    """
    if busy or scanning:
        # Something is already running; the reason for that is shown elsewhere.
        return False, ""
    if from_snapshot:
        # The offline path: reads chainstate_snapshot/ with bitcoind down, so
        # none of the sync checks apply - only that the snapshot is complete.
        return snapshot_exportable, ""
    # A running node at the tip is not enough on its own: with a half-loaded or
    # abandoned chainstate_snapshot/ on disk the export would read a truncated
    # UTXO set, so `require_dumpable_chainstate` refuses both.  This datadir was
    # in the 'orphaned' state on 2026-08-09, which is how the case is known.
    if chainstate == "loading":
        return False, t("settings.utxo.needs_load_done")
    if chainstate == "orphaned":
        return False, t("settings.utxo.orphaned_snapshot")
    if not available:
        return False, t("settings.node.binaries_missing")
    if not running:
        # bitcoin-utxo-dump reads LevelDB off disk, but the export still asks
        # the node where the tip is before it stops it.
        return False, t("settings.utxo.needs_node")
    if not reachable:
        # Up but not answering: a probe taken while the RPC was busy reports
        # every field as zero, which walks straight past the sync checks below
        # and lands on "verification has not finished" - a reason that has
        # nothing to do with what is happening.
        return False, t("settings.utxo.needs_rpc")
    if ibd or (headers and blocks < headers):
        behind = max(headers - blocks, 0)
        return False, t("settings.utxo.needs_sync", behind=group_thousands(behind))
    if progress < _MIN_VERIFICATION_PROGRESS:
        return False, t("settings.utxo.needs_verify")
    return True, ""


def _size_or_zero(path: Path) -> int:
    """
    A file's size, or 0 if it is not there.

    One stat(), never exists()-then-stat().  These files are the temporaries of
    a rebuild in flight: `finalize()` renames the .tmp.db away and the CSV is
    unlinked at the end, so the path can vanish between the two calls.  The
    resulting FileNotFoundError unwound out of the render loop and took the
    whole window down with it.
    """
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _human_bytes(value: int) -> str:
    """Size in the unit that keeps a significant digit - "0.0 GB" says nothing."""
    if value >= 1_000_000_000:
        return f"{value / 1e9:.2f} GB"
    if value >= 1_000_000:
        return f"{value / 1e6:.0f} MB"
    return f"{value / 1e3:.0f} KB"


def _hms(seconds: int) -> str:
    """Elapsed time as m:ss / h:mm:ss - a bare second count reads as noise."""
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


#: Monospace padding that aligns the label/value columns on the node tab.
_LABEL_PAD = 16


@dataclass(frozen=True, slots=True)
class SettingsCallbacks:
    """The few actions only the application can perform."""

    on_save: Callable[[], None]
    on_reload: Callable[[], None]
    on_language: Callable[[str], None]
    on_theme: Callable[[str], None]


class SettingsDialog:
    """Modal configuration editor backed by `config.yaml`."""

    def __init__(self, ctx: PanelContext, settings, bus: EventBus,
                 tasks: services.TaskRunner, callbacks: SettingsCallbacks,
                 *, config_path: Optional[Path] = None) -> None:
        self.ctx = ctx
        self.settings = settings
        self.config_path = config_path
        self.bus = bus
        self.tasks = tasks
        self.cb = callbacks
        # Frames left to re-apply the fit after opening.  The first frame after
        # a rebuild can report a stale viewport size; re-fitting for a couple of
        # frames lets the dialog settle at the right size on its own.
        self._settle = 0
        self._last_node_poll = 0.0
        self._last_rebuild_poll = 0.0
        self._rebuild_started: Optional[float] = None
        self._last_snapshot_check = 0.0
        self._snapshot_dir_state = "none"
        self._last_snapshot_size_poll = 0.0
        self._snapshot_bytes = 0
        self._last_load_poll = 0.0
        self._load_progress: Optional[tuple] = None
        self._snapshot_step = "download"
        self._node_step = "start"
        self._load_when_ready = 0.0

    # ═══════════════════════════════════════════════════════════════════════
    #  Construction
    # ═══════════════════════════════════════════════════════════════════════
    def _px(self, value: int) -> int:
        """
        A pixel size at font scale 1.0, converted to the scale on screen.

        Widget widths are fixed pixels while the text inside them is not, so at
        scale 2.0 the labels outgrew their buttons and were clipped mid-word -
        "INICIAR NODE" rendered as "INICIAR NOD".
        """
        scale = getattr(self.ctx.fonts, "scale", 1.0) or 1.0
        return int(value * scale)

    @property
    def _modal_size(self) -> tuple:
        """
        Modal dimensions for the font size actually on screen.

        Never smaller than the base size: spacers and separators are fixed
        pixels and do not shrink with the font, so a modal scaled down to 0.7
        lost 30% of its height against content that had barely moved - and the
        tab that fitted at 1.0 started needing a scrollbar.  Shrinking buys
        nothing anyway; growing is the half that matters.
        """
        factor = max(1.0, getattr(self.ctx.fonts, "scale", 1.0) or 1.0)
        return int(_MODAL_W * factor), int(_MODAL_H * factor)

    def build(self) -> None:
        ctx = self.ctx

        with dpg.window(tag=_MODAL, label=t("settings.title"), modal=True, show=False,
                        width=self._modal_size[0], height=self._modal_size[1],
                        no_collapse=True,
                        no_scrollbar=True, on_close=self.close) as modal:
            dpg.bind_item_theme(modal, ctx.themes.panel)

            with dpg.tab_bar():
                with dpg.tab(label=t("settings.tab.scanner")):
                    self._build_scanner_tab()
                with dpg.tab(label=t("settings.tab.paths")):
                    self._build_paths_tab()
                with dpg.tab(label=t("settings.tab.node")):
                    self._build_node_tab()
                with dpg.tab(label=t("settings.tab.appearance")):
                    self._build_appearance_tab()

            dpg.add_separator()
            with dpg.group(horizontal=True):
                save = dpg.add_button(label=t("settings.save"), width=self._px(130),
                                      callback=lambda *_: self._save())
                dpg.bind_item_theme(save, ctx.themes.btn_start)

                reload_btn = dpg.add_button(label=t("settings.reload"), width=self._px(140),
                                            callback=lambda *_: self._reload())
                dpg.bind_item_theme(reload_btn, ctx.themes.btn_ghost)

                close = dpg.add_button(label=t("settings.close"), width=self._px(130),
                                       callback=lambda *_: self.close())
                dpg.bind_item_theme(close, ctx.themes.btn_stop)

                styled_text("", color=ctx.palette.text_dim, font=ctx.font("small"),
                            tag=_STATUS)

    # ----- tab 1: scanner ----------------------------------------------------
    def _build_scanner_tab(self) -> None:
        ctx = self.ctx
        s = self.settings

        with dpg.child_window(height=-MODAL_FOOTER_H, border=False):
            styled_text(t("settings.locked_while_running"),
                        color=ctx.palette.text_faint, font=ctx.font("small"),
                        tag="set_lock_note")
            dpg.add_spacer(height=4)

            self._label(t("settings.scanner.mode"))
            dpg.add_combo(["random", "hd"], default_value=s.scanner.mode,
                          tag="set_mode", width=self._px(260))
            styled_text(t("settings.scanner.mode_tip"), color=ctx.palette.text_faint,
                        font=ctx.font("tiny"))

            dpg.add_spacer(height=6)
            self._label(t("settings.scanner.workers"))
            dpg.add_slider_int(tag="set_workers", default_value=s.scanner.workers,
                               min_value=1, max_value=_MAX_WORKERS_UI, width=self._px(420),
                               clamped=True)

            dpg.add_spacer(height=6)
            self._label(t("settings.scanner.queue"))
            dpg.add_input_int(tag="set_queue", default_value=s.scanner.queue_size,
                              min_value=16, max_value=100_000, min_clamped=True,
                              max_clamped=True, step=50, width=self._px(260))

            dpg.add_spacer(height=6)
            self._label(t("settings.scanner.min_balance"))
            dpg.add_input_int(tag="set_minbal", default_value=s.scanner.min_balance_satoshis,
                              min_value=0, min_clamped=True, step=1000, width=self._px(260))
            styled_text(t("settings.scanner.min_balance_tip"), color=ctx.palette.text_faint,
                        font=ctx.font("tiny"))

            dpg.add_spacer(height=6)
            self._label(t("settings.scanner.hd_children"))
            dpg.add_input_int(tag="set_hdchildren", default_value=s.hd_wallet.child_count,
                              min_value=1, max_value=1000, min_clamped=True,
                              max_clamped=True, width=self._px(260))

            dpg.add_spacer(height=10)
            dpg.add_separator()
            self._label(t("settings.scanner.address_types"))
            styled_text(t("settings.scanner.address_types_hint"),
                        color=ctx.palette.text_faint, font=ctx.font("tiny"))
            for key, label in _ADDRESS_TYPES:
                dpg.add_checkbox(label=label, tag=f"set_at_{key}",
                                 default_value=key in s.scanner.address_types)

    # ----- tab 2: paths ------------------------------------------------------
    def _build_paths_tab(self) -> None:
        """
        Read-only map of the installation.

        Every path is derived from the project folder, so there is nothing
        useful to type here - only to look up, open and copy.  Relocating is
        still possible, just not from a text box: `MINING_DARK_DATA_DIR` moves
        everything at once, and `config.yaml` moves individual folders.
        """
        ctx = self.ctx

        with dpg.child_window(height=-MODAL_FOOTER_H, border=False):
            styled_text(t("settings.paths.intro"), color=ctx.palette.text_dim,
                        font=ctx.font("small"), wrap=self._px(860))
            dpg.add_spacer(height=8)

            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                           borders_innerV=False) as table:
                dpg.add_table_column(init_width_or_weight=1.0)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=196)
                dpg.bind_item_theme(table, ctx.themes.table)

                for index, (label, resolver, is_file) in enumerate(self._path_entries()):
                    self._path_row(index, label, resolver, is_file)

            dpg.add_spacer(height=6)
            dpg.add_separator()
            styled_text(t("settings.paths.move_note"), color=ctx.palette.text_faint,
                        font=ctx.font("tiny"), wrap=self._px(860))

    def _path_entries(self):
        """
        (label, resolver, is_file) for every location worth showing.

        The resolvers stay lambdas rather than bound methods because RELOAD
        replaces `settings.output` / `settings.utxo` wholesale - a method bound
        at build time would keep resolving the old model.
        """
        from mining_dark import paths as project_paths

        return (
            (t("settings.paths.project"), lambda: project_paths.PROJECT_ROOT, False),
            (t("settings.paths.data"), lambda: project_paths.DATA_DIR, False),
            (t("settings.paths.found"),
             lambda: self.settings.output.resolved_found_wallets_dir(), False),  # noqa: PLW0108
            (t("settings.paths.logs"),
             lambda: self.settings.logging.resolved_logs_dir(), False),          # noqa: PLW0108
            (t("settings.paths.db"),
             lambda: self.settings.utxo.resolved_db_file(), True),
            # Not configurable: bitcoin_node always drives the node with
            # -datadir=<this>.  Relocate it with MINING_DARK_DATA_DIR.
            (t("settings.paths.core"), lambda: project_paths.BITCOIN_CORE_DIR, False),
            (t("settings.paths.snapshots"), lambda: project_paths.SNAPSHOTS_DIR, False),
            (t("settings.paths.config"),
             lambda: self.config_path or project_paths.CONFIG_FILE, True),
        )

    def _path_row(self, index: int, label: str, resolver, is_file: bool) -> None:
        ctx = self.ctx
        path = Path(resolver())

        with dpg.table_row():
            with dpg.group():
                styled_text(label, color=ctx.palette.accent, font=ctx.font("small"))
                with dpg.group(horizontal=True):
                    styled_text(str(path), color=ctx.palette.text, font=ctx.font("tiny"),
                                tag=f"path_val_{index}")
                    styled_text("", color=ctx.palette.text_faint, font=ctx.font("tiny"),
                                tag=f"path_miss_{index}")

            with dpg.group(horizontal=True):
                open_btn = dpg.add_button(
                    label=t("settings.paths.open"), width=88,
                    callback=lambda *_: self._open_path(resolver, is_file),
                )
                dpg.bind_item_theme(open_btn, ctx.themes.btn_ghost)

                copy_btn = dpg.add_button(
                    label=t("settings.paths.copy"), width=88,
                    callback=lambda *_: self._copy_path(resolver),
                )
                dpg.bind_item_theme(copy_btn, ctx.themes.btn_ghost)

    def _open_path(self, resolver, is_file: bool) -> None:
        """Open a folder, or the folder holding a file that does not exist yet."""
        target = Path(resolver())
        try:
            if is_file:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target = target.parent
            else:
                target.mkdir(parents=True, exist_ok=True)
            services.open_in_desktop(target)
        except OSError as exc:
            self._status(t("wallets.open_failed", error=str(exc)), error=True)

    def _copy_path(self, resolver) -> None:
        path = str(Path(resolver()))
        try:
            dpg.set_clipboard_text(path)
        except SystemError:                        # pragma: no cover - no clipboard
            self._status(path)
            return
        self._status(t("settings.paths.copied", path=path))

    # ----- tab 3: node & utxo ------------------------------------------------
    def _build_node_tab(self) -> None:
        """
        The three steps of getting a UTXO database, in the order they happen.

        One builder per section: together they ran past what a single function
        should be, and the split matches the numbering the tab already shows.
        Each is called inside the child window, so its items still land there.
        """
        ctx = self.ctx
        p = ctx.palette

        with dpg.child_window(height=-MODAL_FOOTER_H, border=False):
            self._label(t("settings.node.snapshot_section"))
            self._build_snapshot_section(ctx, p)

            dpg.add_spacer(height=12)
            dpg.add_separator()
            self._label(t("settings.node.status"))
            self._build_core_section(ctx, p)

            # The snapshot belongs to the node, not to the UTXO database: it is
            # what lets bitcoind skip most of the initial block download.
            dpg.add_spacer(height=10)
            dpg.add_separator()
            self._label(t("settings.utxo.title"))
            self._build_utxo_section(ctx, p)

    def _build_snapshot_section(self, ctx, p) -> None:
        """Step 1: the assumeutxo file - download, cancel, load."""

        with dpg.group(horizontal=True):
            styled_text(t("settings.node.snapshot_absent").ljust(_LABEL_PAD),
                        color=p.text, font=ctx.font("small"), tag="sn_state")

        dpg.add_progress_bar(tag="sn_progress", default_value=0.0, width=-1,
                             height=self._px(12), overlay="")
        dpg.bind_item_theme("sn_progress", ctx.themes.bar_scan_phase)

        dpg.add_spacer(height=4)
        # One button rather than three: download, stop and load are the
        # steps of a single linear job, and only ever one of them is the
        # next thing to do.  Its label says which.
        with dpg.group(horizontal=True):
            action = dpg.add_button(label=t("settings.node.snapshot_download"),
                                    width=self._px(230), tag="sn_btn_action",
                                    callback=lambda *_: self._snapshot_action())
            dpg.bind_item_theme(action, ctx.themes.btn_ghost)
            styled_text("", color=p.text_dim, font=ctx.font("tiny"),
                        tag="sn_reason")

    def _build_core_section(self, ctx, p) -> None:
        """Step 2: bitcoind itself, and the two phases it syncs through."""

        with dpg.group(horizontal=True):
            styled_text("●", color=p.text_faint, font=ctx.font("h2"), tag="nd_dot")
            styled_text(t("settings.node.stopped"), color=p.text_dim,
                        font=ctx.font("h2"), tag="nd_state")

        for tag, label in (("nd_chain", t("settings.node.chain")),
                           ("nd_blocks", t("settings.node.blocks")),
                           ("nd_sync", t("settings.node.sync")),
                           ("nd_disk", t("settings.node.disk")),
                           ("nd_dir", "Datadir")):
            with dpg.group(horizontal=True):
                styled_text(label.ljust(_LABEL_PAD), color=p.text_dim,
                            font=ctx.font("small"))
                styled_text("-", color=p.text, font=ctx.font("small"), tag=tag)

        # Two bars because the node moves through two phases that never
        # advance together: every header arrives before the first block, so
        # a single bar would sit at zero for minutes and then jump.
        styled_text("", color=p.text_dim,
                    font=ctx.font("tiny"), tag="nd_headers_label")
        dpg.add_progress_bar(tag="nd_headers", default_value=0.0, width=-1,
                             height=12, overlay="")
        dpg.bind_item_theme("nd_headers", ctx.themes.bar_scan_phase)

        styled_text("", color=p.text_dim,
                    font=ctx.font("tiny"), tag="nd_blocks_label")
        dpg.add_progress_bar(tag="nd_progress", default_value=0.0, width=-1,
                             height=16, overlay="0.0000%")
        dpg.bind_item_theme("nd_progress", ctx.themes.bar_scan_phase)

        styled_text("", color=p.warning, font=ctx.font("small"), tag="nd_note")

        dpg.add_spacer(height=8)
        # No Refresh button.  Its one exclusive job was the disk probe, and
        # that now rides along with the 3 s poll - so the button did exactly
        # what the panel already does by itself, which is worse than doing
        # nothing: a control that seems not to work when pressed.
        with dpg.group(horizontal=True):
            # One button: start and stop are never both applicable, so
            # showing both left one permanently greyed out beside the other.
            power = dpg.add_button(label=t("settings.node.start"),
                                   width=self._px(190), tag="nd_btn_power",
                                   callback=lambda *_: self._node_power())
            dpg.bind_item_theme(power, ctx.themes.btn_start)

            # A modifier for starting, so it has no meaning while running.
            dpg.add_checkbox(label=t("settings.node.reindex"), tag="nd_reindex")
            styled_text("", color=p.text_dim, font=ctx.font("tiny"),
                        tag="nd_power_reason")


    def _build_utxo_section(self, ctx, p) -> None:
        """Step 3: the local SQLite database the scanner reads."""

        # A rebuild runs for tens of minutes.  Without these the panel just
        # logs "started" and goes silent, which is indistinguishable from a
        # freeze - the detailed Rich progress only reaches the terminal.
        #
        # Directly under the heading rather than below the button: this is
        # the last section of the tallest tab, and down there the note
        # landed behind the footer on a 940px screen - unreadable at
        # exactly the moment it is the only thing worth reading.  Both are
        # hidden when idle, so they cost nothing the rest of the time.
        dpg.add_progress_bar(tag="ut_progress", default_value=0.0, width=-1,
                             height=16, overlay="", show=False)
        dpg.bind_item_theme("ut_progress", ctx.themes.bar_scan_phase)
        styled_text("", color=p.accent, font=ctx.font("tiny"),
                    tag="ut_progress_note", wrap=self._px(820))
        dpg.configure_item("ut_progress_note", show=False)

        for tag, label in (("ut_status", t("footer.db")),
                           ("ut_count", t("footer.addresses")),
                           ("ut_size", t("footer.size")),
                           ("ut_updated", t("footer.updated")),
                           ("ut_disk", t("settings.utxo.disk"))):
            with dpg.group(horizontal=True):
                styled_text(label.ljust(_LABEL_PAD), color=p.text_dim,
                            font=ctx.font("small"))
                styled_text("-", color=p.text, font=ctx.font("small"), tag=tag)

        dpg.add_spacer(height=6)
        with dpg.group(horizontal=True):
            rebuild = dpg.add_button(label=t("settings.utxo.rebuild"), width=self._px(210),
                                     tag="ut_btn_rebuild",
                                     callback=lambda *_: self._utxo_rebuild())
            dpg.bind_item_theme(rebuild, ctx.themes.btn_pause)
            dpg.add_checkbox(label=t("settings.utxo.force"), tag="ut_force")
            dpg.add_checkbox(label=t("settings.utxo.from_snapshot"),
                             tag="ut_from_snapshot")
            # Why the button beside it is greyed out.  Without this a
            # disabled button reads as a broken one, and the step that is
            # missing - a node that is not up, or not yet at the tip - is
            # a section further up the panel.  On the same row as the
            # button rather than below it: the tab is already at the
            # height the screen allows, and an always-present line here
            # was enough to push the last row under a scrollbar.
            styled_text("", color=p.text_dim, font=ctx.font("tiny"),
                        tag="ut_rebuild_reason")

        styled_text("", color=p.text_faint, font=ctx.font("tiny"),
                    tag="ut_disk_need", wrap=self._px(820))


    # ----- tab 4: appearance -------------------------------------------------
    def _build_appearance_tab(self) -> None:
        ctx = self.ctx
        s = self.settings

        with dpg.child_window(height=-MODAL_FOOTER_H, border=False):
            self._label(t("settings.appearance.language"))
            dpg.add_combo(
                [LANGUAGES[code] for code in LANGUAGES],
                default_value=LANGUAGES.get(get_language(), ""),
                tag="set_lang", width=self._px(260), callback=self._on_language,
            )
            styled_text(t("settings.appearance.language_note"),
                        color=ctx.palette.text_faint, font=ctx.font("tiny"))

            dpg.add_spacer(height=10)
            self._label(t("settings.appearance.theme"))
            dpg.add_combo([PALETTES[name].label for name in PALETTES],
                          default_value=PALETTES[s.ui.palette].label,
                          tag="set_theme", width=self._px(260), callback=self._on_theme)

            dpg.add_spacer(height=10)
            self._label(t("settings.appearance.font_scale"))
            dpg.add_slider_float(tag="set_fontscale", default_value=s.ui.font_scale,
                                 min_value=0.7, max_value=2.0, width=self._px(420),
                                 clamped=True, format="%.2fx")
            styled_text(t("settings.appearance.font_note"),
                        color=ctx.palette.text_faint, font=ctx.font("tiny"))

    def _label(self, text: str) -> None:
        styled_text(text, color=self.ctx.palette.accent, font=self.ctx.font("small"))

    # ═══════════════════════════════════════════════════════════════════════
    #  Open / close
    # ═══════════════════════════════════════════════════════════════════════
    def open(self) -> None:
        if not dpg.does_item_exist(_MODAL):
            return
        self.populate()
        # Stale red text from a previous session contradicts the buttons
        # it sits under; nothing else ever cleared it.
        self._status("")
        dpg.configure_item(_MODAL, show=True)
        self._settle = 3
        fit_and_center(_MODAL, max_width=self._modal_size[0],
                       max_height=self._modal_size[1])
        # Arm the poll to fire on the next frame rather than probing here.
        # Submitting directly raced the poll for the TaskRunner, and `submit`
        # declines the loser - so whichever landed first decided what got
        # measured.  The poll usually won, and it was the one that left the
        # disk unprobed, which is why free space could read "-" for a whole
        # session.  One path now, and it always measures everything.
        self._last_node_poll = 0.0

    def close(self) -> None:
        if dpg.does_item_exist(_MODAL):
            dpg.configure_item(_MODAL, show=False)

    @property
    def is_open(self) -> bool:
        return dpg.does_item_exist(_MODAL) and dpg.is_item_shown(_MODAL)

    # ═══════════════════════════════════════════════════════════════════════
    #  Settings <-> widgets
    # ═══════════════════════════════════════════════════════════════════════
    def populate(self) -> None:
        """Push the current `Settings` into the widgets."""
        s = self.settings
        _set("set_mode", s.scanner.mode)
        # No clamp: the slider now covers the whole valid range, and clamping
        # here is what fed the truncated value straight back out through
        # collect().
        _set("set_workers", s.scanner.workers)
        _set("set_queue", s.scanner.queue_size)
        _set("set_minbal", s.scanner.min_balance_satoshis)
        _set("set_hdchildren", s.hd_wallet.child_count)

        for key, _ in _ADDRESS_TYPES:
            _set(f"set_at_{key}", key in s.scanner.address_types)

        _set("set_lang", LANGUAGES.get(get_language(), ""))
        _set("set_theme", PALETTES[s.ui.palette].label)
        _set("set_fontscale", s.ui.font_scale)

    def collect(self) -> None:
        """
        Read the widgets back into `Settings`.

        Mutates the existing object rather than replacing it, because the live
        backend holds a reference to it.
        """
        s = self.settings
        s.scanner.mode = _get("set_mode", s.scanner.mode)
        s.scanner.workers = int(_get("set_workers", s.scanner.workers))
        s.scanner.queue_size = int(_get("set_queue", s.scanner.queue_size))
        s.scanner.min_balance_satoshis = int(
            _get("set_minbal", s.scanner.min_balance_satoshis)
        )
        s.hd_wallet.child_count = int(_get("set_hdchildren", s.hd_wallet.child_count))

        selected = [key for key, _ in _ADDRESS_TYPES if _get(f"set_at_{key}", False)]
        # An empty selection would make the scanner check nothing at all.
        s.scanner.address_types = selected or ["p2pkh"]

        # The Paths tab is read-only, so nothing is written back for it here.
        # A `_get` on a widget that no longer exists returns the default, which
        # would silently wipe any path override set by hand in config.yaml.

        s.ui.language = get_language()
        s.ui.palette = _label_to_palette(_get("set_theme", PALETTES[s.ui.palette].label))
        s.ui.font_scale = round(float(_get("set_fontscale", s.ui.font_scale)), 2)

    # ═══════════════════════════════════════════════════════════════════════
    #  Actions
    # ═══════════════════════════════════════════════════════════════════════
    def _save(self) -> None:
        # collect() is called by the application inside the same try that
        # reports save failures, so a value the config models reject shows up
        # in the dialog instead of raising out of a DearPyGui callback.
        self.cb.on_save()

    def _reload(self) -> None:
        self.cb.on_reload()
        self.populate()

    def _on_language(self, _sender, label: str) -> None:
        for code, name in LANGUAGES.items():
            if name == label:
                self.cb.on_language(code)
                return

    def _on_theme(self, _sender, label: str) -> None:
        self.cb.on_theme(_label_to_palette(label))

    # ----- node / utxo -------------------------------------------------------
    def _poll_node(self) -> None:
        """
        Keep the node readouts live while this dialog is open.

        Nothing used to refresh them on its own, so a syncing node sat frozen
        at whatever it read when the dialog opened and the progress bar never
        moved - the panel looked broken even when the node was healthy.

        The probe is an RPC round-trip, so it goes through the TaskRunner and
        never touches the render thread.  `submit` declines while another job
        holds the runner, which is also what keeps these from stacking up
        behind a rebuild.
        """
        now = time.monotonic()
        if now - self._last_node_poll < _NODE_POLL_PERIOD:
            return
        self._last_node_poll = now

        # The disk probe rides along.  It used to be left out as "too heavy for
        # a timer", which left free space and the rebuild estimate frozen at
        # whatever they read when the dialog opened - stale exactly during a
        # rebuild, when they are the two numbers worth watching, and only ever
        # refreshed by a button whose one job that was.  Measured instead of
        # assumed: 552 files, 9-13 ms, on the runner's thread rather than the
        # render loop.  Once per poll is 0.3% of one core.
        self.tasks.submit(
            "node-status", lambda: services.refresh_node(self.bus, self.settings)
        )

    def _node_power(self) -> None:
        """Start or stop, whichever the button is currently offering."""
        if self._node_step == "stop":
            job = ("node-stop", lambda: services.stop_node(self.bus))
        else:
            reindex = bool(_get("nd_reindex", False))
            job = ("node-start", lambda: services.start_node(self.bus, reindex=reindex))

        if not self.tasks.submit(*job):
            self._status(t("settings.utxo.busy"), error=True)

    def _rebuild_progress(self, disk) -> Optional[tuple]:
        """
        Where the running rebuild has got to, read straight off the temp files.

        Deliberately not routed through the TaskRunner: the rebuild itself
        holds it for the whole run, so anything asking it for progress would
        report nothing for exactly as long as the progress mattered.  Two
        stat() calls per poll cost nothing.

        Returns (fraction, phase_key, done_bytes, expected_bytes) or None when
        no rebuild is in flight.  The expected sizes come from the chainstate
        and are an order of magnitude, not a promise - the byte counts beside
        the bar are the honest number.
        """
        from mining_dark import paths

        csv_path = paths.UTXO_TMP_CSV
        tmp_db = self.settings.utxo.resolved_db_file().with_suffix(".tmp.db")

        # The import phase wins: once the database is being built the CSV is
        # complete and its size no longer says anything about progress.
        db_bytes = self._live_size(tmp_db)
        if db_bytes:
            expected = disk.estimated_db_bytes
            return (
                min(db_bytes / expected, 1.0) if expected else 0.0,
                "settings.utxo.phase_import",
                db_bytes,
                expected,
            )

        csv_bytes = self._live_size(csv_path)
        if csv_bytes:
            expected = disk.estimated_csv_bytes
            return (
                min(csv_bytes / expected, 1.0) if expected else 0.0,
                "settings.utxo.phase_export",
                csv_bytes,
                expected,
            )

        return None

    def _live_size(self, path: Path) -> int:
        """
        A temp file's size, but only if this rebuild is the one writing it.

        Both temp files survive a rebuild that is killed outright - the cleanup
        runs in a `finally`, and nothing runs after SIGKILL, a closed terminal
        or a power cut.  Read at face value, the leftovers made the next
        rebuild open on the *import* phase, frozen at exactly the byte count
        the interrupted run reached: the file is not touched during export, so
        the bar sat there for the tens of minutes that phase takes.

        The mtime settles it.  A file the current run has not written since it
        started belongs to the previous one, and says nothing about this one.
        """
        if self._rebuild_started is None:
            return 0
        try:
            stat = path.stat()
        except OSError:
            return 0
        # Wall clock against a monotonic start, so compare elapsed spans: the
        # file must have been touched no earlier than the rebuild began.
        age = time.time() - stat.st_mtime
        return stat.st_size if age <= time.monotonic() - self._rebuild_started else 0

    def _update_rebuild_progress(self, state: UIState) -> None:
        """Drive the rebuild bar while `utxo-rebuild` holds the task runner."""
        running = state.task == "utxo-rebuild"

        if not running:
            if self._rebuild_started is not None:
                self._rebuild_started = None
                dpg.configure_item("ut_progress", show=False)
                dpg.configure_item("ut_progress_note", show=False)
            return

        now = time.monotonic()
        if self._rebuild_started is None:
            self._rebuild_started = now
            dpg.configure_item("ut_progress", show=True)
            dpg.configure_item("ut_progress_note", show=True)
        if now - self._last_rebuild_poll < _REBUILD_POLL_PERIOD:
            return
        self._last_rebuild_poll = now

        elapsed = int(now - self._rebuild_started)
        progress = self._rebuild_progress(state.disk)

        if progress is None:
            # Between phases, or the export has not written its first block yet.
            dpg.set_value("ut_progress", 0.0)
            dpg.configure_item("ut_progress", overlay="")
            set_text("ut_progress_note",
                     t("settings.utxo.phase_starting", elapsed=_hms(elapsed)))
            return

        fraction, phase, done, expected = progress
        dpg.set_value("ut_progress", fraction)
        dpg.configure_item("ut_progress", overlay=f"{fraction * 100:.1f}%")
        set_text("ut_progress_note", t(
            phase,
            done=_human_bytes(done),
            expected=_human_bytes(expected) if expected else "?",
            elapsed=_hms(elapsed),
        ))

    def _snapshot_state(self) -> str:
        """
        `chainstate_snapshot/` as one of 'none' | 'loading' | 'orphaned' | 'loaded'.

        Cached: `snapshot_dir_state()` is two stat() calls in the common case,
        but for a snapshot left half-loaded it walks the whole directory - over
        a thousand files, measured at 12.9 ms per frame against a 16.7 ms
        budget.  The state changes on the timescale of a node restart, so
        asking once every few seconds is as good as asking 60 times a second.

        The full string rather than a 'loaded' boolean: the two states in
        between are what the rebuild gate has to refuse on, and collapsing them
        into 'not loaded' made them indistinguishable from a datadir that never
        had a snapshot - a case where the rebuild is perfectly fine.
        """
        now = time.monotonic()
        if now - self._last_snapshot_check < _SNAPSHOT_STATE_TTL:
            return self._snapshot_dir_state
        self._last_snapshot_check = now
        from mining_dark import bitcoin_node
        try:
            self._snapshot_dir_state = bitcoin_node.snapshot_dir_state()
        except OSError:                      # pragma: no cover - unreadable datadir
            self._snapshot_dir_state = "none"
        return self._snapshot_dir_state

    def _snapshot_loaded(self) -> bool:
        """Whether this datadir already holds a fully loaded snapshot."""
        return self._snapshot_state() == "loaded"

    def _snapshot_exportable(self, node) -> bool:
        """
        Whether an offline snapshot export would be accepted right now.

        Mirrors what `utxo_updater._announce_snapshot_export` enforces, so the
        checkbox is only offered when pressing REBUILD would actually work.
        """
        return not node.running and self._snapshot_loaded()

    # ----- snapshot ----------------------------------------------------------
    def _snapshot_action(self) -> None:
        """Do whatever the button currently offers.  See _snapshot_step()."""
        step = self._snapshot_step
        if step == "cancel":
            # Stop the download; what is on disk stays and resumes next click.
            services.cancel_snapshot_download()
        elif step == "download":
            if not self.tasks.submit("snapshot-get",
                                     lambda: services.download_snapshot(self.bus)):
                self._status(t("settings.utxo.busy"), error=True)
        elif step == "load":
            if not self.tasks.submit("snapshot-load",
                                     lambda: services.load_snapshot(self.bus)):
                self._status(t("settings.utxo.busy"), error=True)
        elif step == "start_and_load":
            # Start now, load later: header sync takes tens of minutes, so the
            # load is armed rather than chained, keeping each job short enough
            # that STOP stays reachable throughout.
            if self.tasks.submit(
                "node-start", lambda: services.start_node(self.bus, reindex=False)
            ):
                self._load_when_ready = time.monotonic() + _ARM_TIMEOUT
            else:
                self._status(t("settings.utxo.busy"), error=True)

    def _snapshot_action_load(self) -> None:
        if not self.tasks.submit("snapshot-load",
                                 lambda: services.load_snapshot(self.bus)):
            self._status(t("settings.utxo.busy"), error=True)

    def _show_snapshot_load(self, palette) -> None:
        """
        Report `loadtxoutset` from the log while it holds the RPC.

        Read straight off debug.log rather than through the TaskRunner, for the
        same reason the rebuild bar is: the job owns the runner for the whole
        load.  It also owns the node - the RPC that started it holds cs_main -
        so `getblockchaininfo` would block too.  The log is the only thing
        still answering.
        """
        from mining_dark import bitcoin_node

        now = time.monotonic()
        if now - self._last_load_poll >= _REBUILD_POLL_PERIOD:
            self._last_load_poll = now
            self._load_progress = bitcoin_node.snapshot_load_progress()

        progress = self._load_progress
        if progress is None:
            # Between the click and Core's first log line.
            set_text("sn_state", t("settings.node.snapshot_loading_start"),
                     palette.accent)
            dpg.set_value("sn_progress", 0.0)
            dpg.configure_item("sn_progress", overlay="")
            return

        fraction, coins_done, coins_total = progress
        set_text("sn_state", t("settings.node.snapshot_loading",
                               done=group_thousands(coins_done),
                               total=group_thousands(coins_total),
                               pct=f"{fraction * 100:{_PCT_FMT}}"),
                 palette.accent)
        dpg.set_value("sn_progress", fraction)
        dpg.configure_item("sn_progress", overlay="")

    def _update_snapshot(self, state: UIState, node) -> None:
        """
        Drive the snapshot row from the file on disk.

        Same approach as the rebuild bar: the download holds the TaskRunner for
        hours, so anything asking it for progress would be silent for exactly
        as long as the progress mattered.  One stat() per poll instead.
        """
        from mining_dark import snapshot as snap

        busy = state.task in ("snapshot-get", "snapshot-load")
        downloading = state.task == "snapshot-get"

        now = time.monotonic()
        if now - self._last_snapshot_size_poll >= _REBUILD_POLL_PERIOD:
            self._last_snapshot_size_poll = now
            self._snapshot_bytes = _size_or_zero(snap.snapshot_path())
        done = self._snapshot_bytes

        # Core allows one loadtxoutset per datadir, so an already-loaded
        # snapshot means the whole row has nothing left to offer.
        already = self._snapshot_loaded() or bool(node.snapshot_active)

        palette = self.ctx.palette
        total = _SNAPSHOT_EXPECTED_BYTES
        fraction = min(done / total, 1.0) if total else 0.0
        complete = done >= total

        if state.task == "snapshot-load":
            # While loadtxoutset runs the row reports the load, not the
            # download: the file is complete by then, so its bar would sit at
            # 100% through the tens of minutes that actually matter - which
            # reads as a frozen interface and is how a load that was working
            # got killed halfway.
            self._show_snapshot_load(palette)
        else:
            if already:
                label, colour = t("settings.node.snapshot_loaded"), palette.accent
            elif complete:
                label = t("settings.node.snapshot_ready", total=_human_bytes(done))
                colour = palette.accent
            elif done:
                label = t("settings.node.snapshot_partial",
                          done=_human_bytes(done), total=_human_bytes(total))
                colour = palette.text
            else:
                label, colour = t("settings.node.snapshot_absent"), palette.text_dim

            # The percentage rides in the label, not inside the bar: as an
            # overlay it sits on the fill and washes out exactly where the bar
            # is busiest.
            if done and not already and not complete:
                label = f"{label}  ·  {fraction * 100:{_PCT_FMT}}%"

            set_text("sn_state", label, colour)
            dpg.set_value("sn_progress", 1.0 if already else fraction)
            dpg.configure_item("sn_progress", overlay="")

        # What the single button offers next, and why it cannot yet.  Every
        # refusal Core would raise is decided here instead, so a click that was
        # always going to fail is never offered in the first place.
        # Armed until a deadline rather than until the node looks down: there
        # is at least one frame between the click and the TaskEvent arriving in
        # which no start is visible yet, and testing for that disarmed the plan
        # immediately.  A deadline cannot race, and it bounds how long a
        # forgotten arming can sit waiting.
        armed = self._load_when_ready > time.monotonic()
        if armed and (already or state.task == "node-stop"):
            armed = False
            self._load_when_ready = 0.0

        self._snapshot_step, enabled, reason = snapshot_next_step(
            already=already, downloading=downloading, complete=complete,
            busy=busy, node_running=bool(node.running), headers=int(node.headers or 0),
            armed=armed,
        )

        if (armed and not busy and node.running
                and int(node.headers or 0) >= _SNAPSHOT_LOAD_HEIGHT):
            self._load_when_ready = 0.0
            self._snapshot_action_load()

        dpg.configure_item("sn_btn_action", enabled=enabled,
                           label=t(_SNAPSHOT_BUTTON_LABELS[self._snapshot_step]),
                           show=not already)
        set_text("sn_reason", reason, palette.warning)

    def _utxo_rebuild(self) -> None:
        force = bool(_get("ut_force", False))
        from_snapshot = bool(_get("ut_from_snapshot", False))
        if not self.tasks.submit(
            "utxo-rebuild",
            lambda: services.rebuild_utxo(
                self.bus, self.settings, force=force, from_snapshot=from_snapshot
            ),
        ):
            self._status(t("settings.utxo.busy"), error=True)

    def _status(self, message: str, *, error: bool = False) -> None:
        set_text(_STATUS, message,
                 self.ctx.palette.error if error else self.ctx.palette.accent)

    def status(self, message: str, *, error: bool = False) -> None:
        """Public entry point so the app can report save results here."""
        self._status(message, error=error)

    # ═══════════════════════════════════════════════════════════════════════
    #  Per-frame
    # ═══════════════════════════════════════════════════════════════════════
    @staticmethod
    def _behind_network(node) -> bool:
        """
        True when the node believes it is synced but the peers are ahead.

        Gated on `not ibd` on purpose: a node still downloading is behind by
        design, and the progress bar above already says so.  What this catches
        is the node that stopped advancing and does not know it.
        """
        if node.ibd or node.peer_height <= 0 or node.blocks <= 0:
            return False
        return node.peer_height - node.blocks > _MAX_BLOCKS_BEHIND

    def _update_node_status(self, state: UIState, node, p) -> None:
        """
        The dot, the word beside it, and the note underneath.

        Five mutually exclusive readings of the same node.  Split out of
        `update()` because this chain is the only real branching in it, and
        keeping it inline pushed that method past what one function should be
        deciding on its own.
        """
        # Starting waits for the RPC to answer, which takes from a few seconds
        # to minutes on a large block index.  Saying nothing for that long is
        # indistinguishable from a dead button, and invites a second click that
        # only starts a second doomed bitcoind.
        if state.task in ("node-start", "node-stop"):
            key = ("settings.node.starting" if state.task == "node-start"
                   else "settings.node.stopping")
            set_text("nd_dot", "●", p.warning)
            set_text("nd_state", t(key), p.warning)
            set_text("nd_note", t("settings.node.wait_note"), p.text_dim)
        elif not node.available:
            set_text("nd_dot", "●", p.error)
            set_text("nd_state", "-", p.error)
            set_text("nd_note", node.detail, p.error)
        elif state.task == "utxo-rebuild":
            # The rebuild stops bitcoind partway through - the shutdown is what
            # flushes the coins cache - and it holds the TaskRunner throughout,
            # so no probe can land and these readouts freeze at whatever they
            # said when it began.  Left alone the section insists the node is
            # running while it is being shut down under it, which reads as the
            # rebuild having broken something.
            set_text("nd_dot", "●", p.warning)
            set_text("nd_state", t("settings.node.busy_rebuild"), p.warning)
            set_text("nd_note", t("settings.utxo.node_busy"), p.text_dim)
        elif node.running and node.invalid_height:
            # Terminal, and invisible to every other readout here: the blocks
            # and sync figures come from the node's own view, which reads as
            # caught up while the chain is walled off above the tip.
            set_text("nd_dot", "●", p.error)
            set_text("nd_state", t("settings.node.blocked"), p.error)
            set_text("nd_note", t("settings.node.blocked_note",
                                  height=f"{node.invalid_height:,}"), p.error)
        elif node.running and self._behind_network(node):
            set_text("nd_dot", "●", p.warning)
            set_text("nd_state", t("settings.node.stalled"), p.warning)
            set_text("nd_note", t("settings.node.stalled_note",
                                  behind=f"{node.peer_height - node.blocks:,}"),
                     p.warning)
        elif node.running:
            set_text("nd_dot", "●", p.accent)
            set_text("nd_state", t("settings.node.running"), p.accent)
            set_text("nd_note", t("settings.node.snapshot") if node.snapshot_active
                     else "", p.warning)
        else:
            # A node that died during startup is still "stopped", but pressing
            # START again only repeats the crash - so say why it is down.
            failed = bool(node.detail)
            set_text("nd_dot", "●", p.error if failed else p.text_faint)
            set_text("nd_state",
                     t("settings.node.start_failed") if failed
                     else t("settings.node.stopped"),
                     p.error if failed else p.text_dim)

            note = node.detail
            # A node that will not start plus an intact snapshot is a dead end
            # with a way out: pressing START again only repeats the crash, while
            # the UTXO set right there is complete and exportable.
            if failed and self._snapshot_exportable(node):
                note = f"{note}  {t('settings.node.use_snapshot')}"
            set_text("nd_note", note, p.error)

    def _update_utxo_readouts(self, state: UIState, p) -> None:
        """The database figures and the disk line under them."""
        db = state.database
        set_text("ut_status", t(f"db.{db.status.value.lower()}"))
        set_text("ut_count", group_thousands(db.address_count) if db.address_count else "-")
        set_text("ut_size", f"{db.size_mb:,.1f} MB" if db.size_mb else "-")
        set_text("ut_updated", db.last_updated or "-")

        disk = state.disk
        if disk.total_bytes:
            set_text("ut_disk", t("settings.utxo.disk_value",
                                  free=_gb(disk.free_bytes),
                                  total=_gb(disk.total_bytes)),
                     p.text if disk.sufficient else p.error)
            need = t("settings.utxo.disk_need",
                     need=_gb(disk.estimated_rebuild_bytes))
            if not disk.sufficient:
                need = f"{t('settings.utxo.disk_tight')}  -  {need}"
            set_text("ut_disk_need", need,
                     p.text_faint if disk.sufficient else p.error)

    def update(self, state: UIState) -> None:
        """Refresh the live readouts.  Cheap enough to run only while open."""
        if not self.is_open:
            return

        if self._settle:
            self._settle -= 1
            fit_and_center(_MODAL, max_width=self._modal_size[0],
                       max_height=self._modal_size[1])

        self._poll_node()
        self._update_rebuild_progress(state)

        p = self.ctx.palette
        self._update_paths(p)
        node = state.node
        busy = bool(blocking_task(state.task))
        scanning = state.run_state in (RunState.RUNNING, RunState.PAUSED,
                                       RunState.STARTING)

        # ----- node ----------------------------------------------------------
        self._update_node_status(state, node, p)

        set_text("nd_chain", node.chain if node.reachable else "-")
        set_text("nd_blocks",
                 f"{group_thousands(node.blocks)} / {group_thousands(node.headers)}"
                 if node.reachable else "-")
        set_text("nd_sync", f"{node.progress * 100:.4f}%" if node.reachable else "-")
        set_text("nd_disk", f"{node.size_bytes / 1e9:.2f} GB" if node.reachable else "-")
        set_text("nd_dir", node.detail if node.available and node.running else "-")

        # Headers first.  Once blocks start arriving the header phase is over
        # by definition, so the top bar parks at 100% rather than resetting.
        # `blocks` alone, deliberately: verificationprogress is not zero during
        # the header phase - it reports values like 7e-10 - so testing it here
        # parked the header bar at 100% while the headers were still arriving.
        headers_done = node.blocks > 0
        header_fraction = 1.0 if headers_done else node.header_progress
        # The labels name the phase that is actually happening, so the panel
        # says where the node is instead of explaining what will happen later.
        # The percentage lives in the label, not inside the bar: as an overlay
        # it sits on top of the fill and washes out exactly where the bar is
        # busiest, and nothing tied it to the phase it belonged to.
        header_pct = f"  ·  {header_fraction * 100:{_PCT_FMT}}%" if node.reachable else ""
        set_text("nd_headers_label",
                 t("settings.node.phase_headers") + header_pct,
                 p.text_dim if headers_done else p.accent)

        blocks_pct = (f"  ·  {node.progress * 100:{_PCT_FMT}}%"
                      if headers_done and node.reachable else "")
        set_text("nd_blocks_label",
                 t("settings.node.phase_blocks") + blocks_pct,
                 p.accent if headers_done else p.text_faint)

        dpg.set_value("nd_headers", header_fraction)
        dpg.configure_item(
            "nd_headers",
            overlay=(group_thousands(node.header_height)
                     if node.header_height and not headers_done else ""),
        )

        dpg.set_value("nd_progress", node.progress)
        dpg.configure_item("nd_progress", overlay="")

        self._node_step, power_on, power_reason = node_power_step(
            available=bool(node.available), running=bool(node.running),
            task=blocking_task(state.task),
            detail=node.detail if not node.available else "",
        )
        dpg.configure_item("nd_btn_power", enabled=power_on,
                           label=t(f"settings.node.{self._node_step}"))
        dpg.bind_item_theme("nd_btn_power",
                            self.ctx.themes.btn_stop if self._node_step == "stop"
                            else self.ctx.themes.btn_start)
        # Reindex only modifies a start, so it disappears while the node is up.
        dpg.configure_item("nd_reindex", show=self._node_step == "start",
                           enabled=power_on)
        set_text("nd_power_reason", power_reason, self.ctx.palette.text_dim)
        self._update_snapshot(state, node)

        # The snapshot export needs a validated chainstate_snapshot/ and a
        # stopped node; offering it otherwise would only produce a refusal.
        snapshot_ready = self._snapshot_exportable(node)
        # Hidden unless it is the way out: this exports the UTXO set straight
        # from chainstate_snapshot/ with bitcoind down, which only matters when
        # the node will not start and the snapshot on disk is intact.  Shown
        # the rest of the time it is a disabled checkbox nobody can use, next
        # to a button people press routinely.
        dpg.configure_item("ut_from_snapshot", show=snapshot_ready,
                           enabled=snapshot_ready and not busy and not scanning)
        if not snapshot_ready:
            dpg.set_value("ut_from_snapshot", False)

        rebuild_on, rebuild_reason = utxo_rebuild_step(
            busy=busy, scanning=scanning,
            available=bool(node.available), running=bool(node.running),
            reachable=bool(node.reachable),
            ibd=bool(node.ibd), blocks=node.blocks, headers=node.headers,
            progress=node.progress,
            from_snapshot=snapshot_ready and bool(dpg.get_value("ut_from_snapshot")),
            snapshot_exportable=snapshot_ready,
            chainstate=self._snapshot_state(),
        )
        # The label is the clearest "it started" there is.  A button that keeps
        # its idle caption and its lit theme while the job runs reads as a click
        # that never registered - and the bar below it only starts moving once
        # bitcoind has finished shutting down, minutes later.
        rebuilding = state.task == "utxo-rebuild"
        dpg.configure_item(
            "ut_btn_rebuild", enabled=rebuild_on,
            label=t("settings.utxo.rebuilding" if rebuilding else "settings.utxo.rebuild"),
        )
        set_text("ut_rebuild_reason", rebuild_reason, self.ctx.palette.text_dim)
        # The disk estimate answers "can I start this?", so it has nothing to
        # say once the answer is yes and it is running.  Dropping it frees the
        # line the progress note needs: the tab is already as tall as the
        # screen allows, and the note was landing behind the footer.
        dpg.configure_item("ut_disk_need", show=not rebuilding)

        # ----- utxo ----------------------------------------------------------
        self._update_utxo_readouts(state, p)

        # ----- scanner tab lock ----------------------------------------------
        for tag in ("set_mode", "set_workers", "set_queue", "set_hdchildren"):
            dpg.configure_item(tag, enabled=not scanning)
        for key, _ in _ADDRESS_TYPES:
            dpg.configure_item(f"set_at_{key}", enabled=not scanning)
        set_text("set_lock_note",
                 t("settings.locked_while_running") if scanning else "",
                 p.warning)

    def _update_paths(self, p) -> None:
        """
        Keep the path list truthful.

        Folders appear as the project uses them - `data/found_wallets/` only
        exists once something has been written - so the "(not created yet)"
        marker has to be re-evaluated, not baked in at build time.
        """
        for index, (_, resolver, _is_file) in enumerate(self._path_entries()):
            path = Path(resolver())
            set_text(f"path_val_{index}", str(path))
            set_text(f"path_miss_{index}",
                     "" if path.exists() else t("settings.paths.missing"),
                     p.text_faint)


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════
def _set(tag: str, value) -> None:
    if dpg.does_item_exist(tag):
        dpg.set_value(tag, value)


def _get(tag: str, default):
    return dpg.get_value(tag) if dpg.does_item_exist(tag) else default


def _gb(value: int) -> str:
    """Bytes as GB, grouped like every other number in the project."""
    return f"{value / 1e9:,.1f} GB"


def _label_to_palette(label: str) -> str:
    for name, palette in PALETTES.items():
        if label in (palette.label, name):
            return name
    return "matrix"

