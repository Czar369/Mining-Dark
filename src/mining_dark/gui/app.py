"""
Mining-Dark graphical dashboard.

Assembles the panels, owns the Dear PyGui render loop and mediates between the
UI widgets, the settings dialog and whichever `ScanBackend` is driving the
session.

Threading contract: this module - and only this module - runs on the main
thread and touches Dear PyGui.  Backends and the `TaskRunner` push telemetry
onto an `EventBus` which is drained here, once per frame, before the panels
redraw.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Optional

import dearpygui.dearpygui as dpg
from loguru import logger

from mining_dark.gui import services
from mining_dark.gui.backends.base import ScanBackend
from mining_dark.i18n import DEFAULT_LANGUAGE, language_label, set_language, t
from mining_dark.gui.panels.activity import ActivityPanel
from mining_dark.gui.panels.common import PanelContext
from mining_dark.gui.panels.footer import FooterCallbacks, FooterPanel
from mining_dark.gui.panels.header import HeaderPanel
from mining_dark.gui.panels.logs import LogPanel
from mining_dark.gui.panels.settings import SettingsCallbacks, SettingsDialog
from mining_dark.gui.panels.wallets import WalletsDialog
from mining_dark.gui.panels.workers import WorkersPanel
from mining_dark.gui.state import (
    DatabaseEvent,
    DBStatus,
    EventBus,
    LogEvent,
    LogLevel,
    RunState,
    UIState,
)
from mining_dark.gui.theme import (
    DEFAULT_PALETTE,
    PALETTES,
    Fonts,
    build_fonts,
    build_themes,
    destroy_themes,
)

#: STREAM LOG severity -> the loguru level that persists it.  Needed because
#: `LogLevel.WARNING.value` is "WARN", which loguru does not know.
_FILE_LEVEL: dict = {
    LogLevel.DEBUG: "DEBUG",
    LogLevel.INFO: "INFO",
    LogLevel.SUCCESS: "SUCCESS",
    LogLevel.WARNING: "WARNING",
    LogLevel.ERROR: "ERROR",
}

_ROOT = "root_window"
_BODY = "body_table"

_MIN_BODY_H = 220

# Vertical chrome the root window spends on padding and item spacing.
_CHROME_H = 16 + 10

# How long closing the window waits for the scan to finish writing.  Covers
# file_manager._DRAIN_TIMEOUT plus the hand-written rescue that follows it: the
# backlog is found wallets, and in random mode a lost one is unrecoverable.
_SHUTDOWN_DRAIN_TIMEOUT = 45.0


class MiningDarkGUI:
    """The dashboard window: three panels, a header strip and a control footer."""

    def __init__(
        self,
        backend: ScanBackend,
        bus: EventBus,
        *,
        settings=None,
        config_path: Optional[Path] = None,
        palette_name: Optional[str] = None,
        language: Optional[str] = None,
        font_scale: Optional[float] = None,
        autostart: bool = False,
        width: int = 1600,
        height: int = 940,
    ) -> None:
        self.backend = backend
        self.bus = bus
        self.settings = settings
        # Whichever file this session was launched with; SAVE must write back
        # to that one, not to the default config.yaml.
        self.config_path = Path(config_path) if config_path else None
        self.autostart = autostart
        self.width = width
        self.height = height

        ui = getattr(settings, "ui", None)
        scanner = getattr(settings, "scanner", None)

        # Explicit arguments (CLI flags) win over config.yaml.
        chosen = palette_name or (ui.palette if ui else DEFAULT_PALETTE)
        self.palette_name = chosen if chosen in PALETTES else DEFAULT_PALETTE
        self.language = set_language(language or (ui.language if ui else DEFAULT_LANGUAGE))
        self.font_scale = font_scale if font_scale is not None else (
            ui.font_scale if ui else 1.0
        )

        self.mode = scanner.mode if scanner else "random"
        self.worker_count = scanner.workers if scanner else 10

        self.state = UIState(worker_count=self.worker_count)
        self.fonts = Fonts()
        self.themes = None
        self.ctx: Optional[PanelContext] = None
        self.tasks = services.TaskRunner(bus)

        self.settings_dialog: Optional[SettingsDialog] = None
        self.wallets_dialog: Optional[WalletsDialog] = None
        self._reopen_settings = False
        # (frame_index, callable) work queued for a later frame
        self._deferred: list[tuple[int, Callable[[], None]]] = []

        # Screenshot support - used by the smoke test and by `--screenshot`.
        self._capture_path: Optional[Path] = None
        self._capture_after: int = 0
        self._frame_index: int = 0

        # Dialogs whose update() has already failed once, so the log records
        # the fault without repeating it at the frame rate.
        self._dialog_faults: set = set()

    # ═══════════════════════════════════════════════════════════════════════
    #  Lifecycle
    # ═══════════════════════════════════════════════════════════════════════
    def run(self, *, screenshot: Optional[Path] = None, screenshot_frames: int = 120,
            max_frames: int = 0) -> None:
        """
        Open the window and block until it closes.

        `screenshot` / `max_frames` exist so the dashboard can be rendered
        headlessly in CI or captured for documentation without a human closing
        the window.
        """
        self._capture_path = Path(screenshot) if screenshot else None
        self._capture_after = screenshot_frames

        dpg.create_context()

        # Fonts must be registered before setup_dearpygui() bakes the atlas.
        self.fonts = build_fonts(self.font_scale)
        self._apply_palette(self.palette_name, rebuild_ui=False)
        self._build_ui()

        dpg.create_viewport(
            title="Mining-Dark  ·  Bitcoin Balance Scanner Pro",
            width=self.width,
            height=self.height,
            min_width=1180,
            min_height=740,
            clear_color=list(self.ctx.palette.bg_deep),
        )
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window(_ROOT, True)
        dpg.set_viewport_resize_callback(lambda *_: self._layout())

        if self.fonts.body:
            dpg.bind_font(self.fonts.body)

        self._layout()
        self._greet()

        if self.autostart:
            self._on_start()

        try:
            while dpg.is_dearpygui_running():
                self._frame()
                dpg.render_dearpygui_frame()
                self._frame_index += 1

                if self._capture_path and self._frame_index == self._capture_after:
                    self._capture_path.parent.mkdir(parents=True, exist_ok=True)
                    dpg.output_frame_buffer(str(self._capture_path))

                if max_frames and self._frame_index >= max_frames:
                    break
        finally:
            # Long enough to cover the persistence drain.  The backend thread
            # is a daemon, so whatever it has not written when this returns
            # dies with the process - and a found wallet in random mode cannot
            # be regenerated.  3 s against a 30 s drain budget meant closing
            # the window threw the backlog away.
            self.backend.stop(timeout=_SHUTDOWN_DRAIN_TIMEOUT)
            self.tasks.join(timeout=2.0)

            # One last drain, purely for the log file.  `_mirror_logs` runs per
            # frame, and the lines that close a session - "scan stopped", the
            # final counts, anything that failed on the way out - are emitted
            # by the stop() above, after the last frame there will ever be.
            # Without this the file just ends mid-session whenever the window
            # is closed directly instead of stopped with the button.
            self._mirror_logs(self.bus.drain())
            dpg.destroy_context()

    # ═══════════════════════════════════════════════════════════════════════
    #  Construction
    # ═══════════════════════════════════════════════════════════════════════
    def _apply_palette(self, name: str, *, rebuild_ui: bool = True) -> None:
        """
        Swap the accent colour scheme.

        The old theme objects are freed only *after* the new widget tree is up,
        so no live item ever points at a deleted theme.
        """
        previous = self.themes

        self.palette_name = name
        palette = PALETTES[name]
        self.themes = build_themes(palette)
        self.ctx = PanelContext(palette=palette, themes=self.themes, fonts=self.fonts)
        dpg.bind_theme(self.themes.global_theme)

        if rebuild_ui:
            self._build_ui()
            self._layout()
            dpg.set_viewport_clear_color(list(palette.bg_deep))

        if previous is not None:
            destroy_themes(previous)

    def _build_ui(self) -> None:
        """Create (or recreate) the whole widget tree."""
        if dpg.does_item_exist(_ROOT):
            dpg.delete_item(_ROOT)

        ctx = self.ctx
        self.header = HeaderPanel(ctx, on_open_settings=self._open_settings,
                                  on_open_wallets=self._open_wallets)
        self.workers_panel = WorkersPanel(ctx)
        self.activity_panel = ActivityPanel(ctx, simulated=self._is_simulated)
        self.log_panel = LogPanel(ctx, on_clear=self._on_clear_logs)
        self.footer = FooterPanel(ctx, FooterCallbacks(
            on_start=self._on_start,
            on_pause=self._on_pause,
            on_stop=self._on_stop,
        ))

        with dpg.window(tag=_ROOT, no_title_bar=True, no_resize=True, no_move=True,
                        no_collapse=True, no_scrollbar=True):
            self.header.build()

            with dpg.table(tag=_BODY, header_row=False, resizable=True,
                           policy=dpg.mvTable_SizingStretchProp,
                           borders_innerV=False, borders_outerV=False,
                           borders_innerH=False, borders_outerH=False):
                dpg.add_table_column(init_width_or_weight=0.235)
                dpg.add_table_column(init_width_or_weight=0.440)
                dpg.add_table_column(init_width_or_weight=0.325)

                with dpg.table_row():
                    self.workers_panel.build(len(self.state.workers))
                    self.activity_panel.build()
                    self.log_panel.build()

            self.footer.build()

        self._build_dialogs()

        # A rebuild (theme or language switch) creates a brand new root window,
        # and the "primary window" flag lived on the one we just deleted -
        # without this the dashboard collapses to its minimum size.
        if dpg.is_viewport_ok():
            dpg.set_primary_window(_ROOT, True)

        self._sync_header()

        # Replay the buffered log into the freshly built panel.
        self.state.pending_logs = list(self.state.logs)
        self.state.dirty_workers = self.state.dirty_recent = True

    def _build_dialogs(self) -> None:
        """
        Dialogs are top-level windows, so deleting the root does not take them
        with it - they have to be torn down and rebuilt explicitly.
        """
        for tag in ("settings_modal", "wallets_modal"):
            if dpg.does_item_exist(tag):
                dpg.delete_item(tag)

        self.settings_dialog = SettingsDialog(
            self.ctx, self.settings, self.bus, self.tasks,
            SettingsCallbacks(
                on_save=self._on_save_settings,
                on_reload=self._on_reload_settings,
                on_language=self._on_language_change,
                on_theme=self._on_theme_change,
            ),
            config_path=self.config_path,
        )
        self.settings_dialog.build()

        self.wallets_dialog = WalletsDialog(
            self.ctx, self.settings,
            on_log=lambda message: self._log(LogLevel.INFO, message),
        )
        self.wallets_dialog.build()

    def _layout(self) -> None:
        """
        Give the three body panels an explicit pixel height.

        Dear PyGui table cells do not constrain a child window's `height=-1`,
        so the split has to be computed rather than inferred.
        """
        client_h = dpg.get_viewport_client_height()
        body_h = max(_MIN_BODY_H,
                     client_h - HeaderPanel.height(self.ctx)
                     - FooterPanel.height(self.ctx) - self.ctx.px(_CHROME_H))

        for tag in ("workers_panel", "activity_panel", "log_panel"):
            if dpg.does_item_exist(tag):
                dpg.configure_item(tag, height=body_h)

    def _greet(self) -> None:
        self._log(LogLevel.INFO, t("log.gui_ready"))
        self._log(LogLevel.INFO, t("log.backend_loaded", backend=self.backend.name,
                                   mode=self.mode, workers=self.worker_count))
        self._log(LogLevel.DEBUG, t("log.no_keys_on_screen"))

        if self._is_simulated:
            self.bus.emit(DatabaseEvent(status=DBStatus.SIMULATED))
        else:
            self._refresh_database()

    # ═══════════════════════════════════════════════════════════════════════
    #  Frame
    # ═══════════════════════════════════════════════════════════════════════
    def _frame(self) -> None:
        now = time.monotonic()
        self._run_deferred()

        events = self.bus.drain()
        if events:
            self._mirror_logs(events)
            self.state.apply(events)

        self.header.update(self.state, now)
        self.workers_panel.update(self.state, now)
        self.activity_panel.update(self.state, now)
        self.log_panel.update(self.state, now)
        self.footer.update(self.state, now)

        # Guarded because these two read the filesystem while a rebuild renames
        # files underneath them.  An exception here used to unwind through the
        # render loop and destroy the window - losing the running scan with it.
        # The dashboard itself stays unguarded: a fault there is a real bug and
        # should surface, not be swallowed frame after frame.
        self._update_dialog(self.settings_dialog, self.state)
        self._update_dialog(self.wallets_dialog)

    def _mirror_logs(self, events: list) -> None:
        """
        Write every STREAM LOG line to the log files as well.

        The panel is fed by the `EventBus`, which never touched loguru - so a
        graphical session left nothing on disk at all, and a scan that failed
        overnight took its only log down with the window when it closed.

        Done here, at the drain, rather than at each `bus.emit()`: this is the
        one point every line the panel will show has to pass through, so the
        file cannot end up with lines the screen never had.  The reverse is
        possible and intended - the panel renders every level, while the sinks
        honour `logging.level` from config.yaml, so at the default INFO the
        DEBUG chatter stays on screen and out of the file.  The panel holds the
        last 400 lines; the file holds the session.

        The sinks apply `_no_secret_filter` exactly as they do for core calls.
        """
        for ev in events:
            if isinstance(ev, LogEvent):
                logger.log(_FILE_LEVEL[ev.file_level or ev.level], ev.message)

    def _update_dialog(self, dialog, *args) -> None:
        if dialog is None:
            return
        try:
            dialog.update(*args)
        except Exception as exc:                          # noqa: BLE001
            name = type(dialog).__name__
            if name not in self._dialog_faults:
                # Once per dialog: at 60 fps a recurring fault would bury the
                # log panel under thousands of identical lines.
                self._dialog_faults.add(name)
                self._log(LogLevel.ERROR, f"{name}: {exc}")

    # ═══════════════════════════════════════════════════════════════════════
    #  Transport callbacks
    # ═══════════════════════════════════════════════════════════════════════
    def _on_start(self) -> None:
        if self.backend.is_running:
            return
        self.state.reset_counters()
        self.state.run_state = RunState.STARTING
        self.backend.start(mode=self.mode, workers=self.worker_count)

    def _on_pause(self) -> None:
        self.backend.toggle_pause()

    def _on_stop(self) -> None:
        self.backend.stop(timeout=0.0)     # non-blocking: do not stall the UI
        self._log(LogLevel.WARNING, t("log.stop_requested"),
                  file_level=LogLevel.INFO)

    def _on_clear_logs(self) -> None:
        self.state.clear_logs()
        self.log_panel.clear()

    # Mode and worker count are edited in the settings dialog and land here via
    # _adopt_settings() on save, so there are no instant-apply handlers.

    # ═══════════════════════════════════════════════════════════════════════
    #  Appearance and configuration
    # ═══════════════════════════════════════════════════════════════════════
    def _on_theme_change(self, name: str) -> None:
        if name not in PALETTES or name == self.palette_name:
            return
        if self.settings is not None:
            self.settings.ui.palette = name

        self._reopen_settings = self._settings_is_open()
        self._apply_palette(name)
        self._restore_dialog()
        self._log(LogLevel.INFO, t("log.theme_changed", theme=PALETTES[name].label))

    def _on_language_change(self, code: str) -> None:
        if code == self.language:
            return
        self.language = set_language(code)
        if self.settings is not None:
            self.settings.ui.language = self.language

        # Every static label was baked at build time, so the tree is rebuilt.
        self._reopen_settings = self._settings_is_open()
        self._build_ui()
        self._layout()
        self._restore_dialog()
        self._log(LogLevel.INFO,
                  t("log.language_changed", language=language_label(self.language)))

    def _on_save_settings(self) -> None:
        from mining_dark.config.settings import save_settings

        if self.settings is None:
            return
        try:
            # ValidationError subclasses ValueError, so a rejected field lands
            # in the same status line as a failed write.
            self.settings_dialog.collect()
            path = save_settings(self.settings, self.config_path)
        except (OSError, ValueError) as exc:
            message = t("settings.save_failed", error=str(exc))
            self._log(LogLevel.ERROR, message)
            self._dialog_status(message, error=True)
            return

        self._adopt_settings()
        message = t("settings.saved", path=str(path))
        self._log(LogLevel.SUCCESS, message)
        self._dialog_status(message)
        self._refresh_database()

    def _on_reload_settings(self) -> None:
        from mining_dark.config.settings import ConfigError, load_settings

        if self.settings is None:
            return

        try:
            # Reload exists to pick up hand edits, so a broken file is the
            # expected failure here - it must not raise out of the callback.
            fresh = load_settings(self.config_path)
        except ConfigError as exc:
            message = t("settings.save_failed", error=str(exc))
            self._log(LogLevel.ERROR, message)
            self._dialog_status(message, error=True)
            return

        # Mutate in place: the live backend holds a reference to this object.
        for section in ("scanner", "hd_wallet", "output", "logging", "utxo", "ui"):
            setattr(self.settings, section, getattr(fresh, section))

        self._adopt_settings()
        self._dialog_status(t("settings.reloaded"))
        self._log(LogLevel.INFO, t("settings.reloaded"))
        self._refresh_database()

        # Two independent ifs, not an if/elif: a file that changed both used to
        # apply only the language, leaving `settings.ui.palette` naming a
        # palette the screen was not using.  Palette first - it is the cheap
        # one, and a language change rebuilds the whole tree, which then gets
        # built with the colours already in place instead of the old ones.
        # Both callbacks return immediately when nothing actually changed.
        self._on_theme_change(self.settings.ui.palette)
        self._on_language_change(self.settings.ui.language)

    def _apply_font_scale(self, scale: float) -> None:
        """
        Resize the interface without a restart.

        The atlas is baked once at startup, so the new size is served by
        `set_global_font_scale` stretching the glyphs already there.  It is
        slightly softer than a freshly baked atlas until the next launch, which
        is a far better trade than the old behaviour: the setting appeared to do
        nothing at all, and the note told people to reopen a window that was
        never going to help.
        """
        if abs(scale - self.font_scale) < 0.01:
            return

        self.font_scale = scale
        baked = self.fonts.baked_scale or 1.0
        dpg.set_global_font_scale(scale / baked)
        self.fonts.scale = scale

        # Rebuild so every width computed from the scale is recomputed too.
        self._reopen_settings = self._settings_is_open()
        self._apply_palette(self.palette_name)
        self._restore_dialog()

    def _adopt_settings(self) -> None:
        """Pull runtime values back out of `Settings` after a save or reload."""
        self.mode = self.settings.scanner.mode
        self.worker_count = self.settings.scanner.workers
        self.state.set_worker_count(self.worker_count)
        self._sync_header()
        self._apply_font_scale(self.settings.ui.font_scale)

    # ═══════════════════════════════════════════════════════════════════════
    #  Dialogs
    # ═══════════════════════════════════════════════════════════════════════
    def _open_settings(self) -> None:
        if self.settings_dialog is not None:
            self.settings_dialog.open()

    def _open_wallets(self) -> None:
        if self.wallets_dialog is not None:
            self.wallets_dialog.open()

    def _settings_is_open(self) -> bool:
        return self.settings_dialog is not None and self.settings_dialog.is_open

    def _restore_dialog(self) -> None:
        """
        Reopen the settings dialog after a rebuild closed it.

        Deferred by a frame on purpose: this runs inside a widget callback, so
        the old modal is still on Dear ImGui's popup stack and is only really
        destroyed at the end of the frame.  Showing the replacement now makes
        ImGui dismiss it immediately, which fires `on_close` and leaves the
        dialog invisible.
        """
        if self._reopen_settings:
            self._reopen_settings = False
            self._defer(2, self._open_settings)

    def _defer(self, frames: int, action: Callable[[], None]) -> None:
        """Run `action` `frames` frames from now, on the render thread."""
        self._deferred.append((self._frame_index + max(1, frames), action))

    def _run_deferred(self) -> None:
        if not self._deferred:
            return
        due = [fn for at, fn in self._deferred if self._frame_index >= at]
        self._deferred = [(at, fn) for at, fn in self._deferred
                          if self._frame_index < at]
        for action in due:
            action()

    def _dialog_status(self, message: str, *, error: bool = False) -> None:
        if self.settings_dialog is not None:
            self.settings_dialog.status(message, error=error)

    # ═══════════════════════════════════════════════════════════════════════
    #  Helpers
    # ═══════════════════════════════════════════════════════════════════════
    @property
    def _is_simulated(self) -> bool:
        return self.backend.__class__.__name__ == "SimulatedBackend"

    def _refresh_database(self) -> None:
        """Read the UTXO database health off the render thread."""
        if self.settings is None or self._is_simulated:
            return
        self.tasks.submit(
            "db-status",
            lambda: self.bus.emit(services.read_database(self.settings)),
        )

    def _sync_header(self) -> None:
        if dpg.does_item_exist("hdr_mode"):
            dpg.set_value("hdr_mode", self.mode.upper())
        if dpg.does_item_exist("hdr_workers"):
            dpg.set_value("hdr_workers", str(self.worker_count))
        if dpg.does_item_exist("hdr_backend"):
            dpg.set_value("hdr_backend", self.backend.name.upper())

    def _log(self, level: LogLevel, message: str,
             file_level: Optional[LogLevel] = None) -> None:
        self.bus.emit(LogEvent(level, message, file_level=file_level))


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════════
def run_gui(
    *,
    simulate: bool = False,
    settings=None,
    config_path: Optional[Path] = None,
    palette: Optional[str] = None,
    language: Optional[str] = None,
    font_scale: Optional[float] = None,
    autostart: bool = False,
    screenshot: Optional[Path] = None,
    screenshot_frames: int = 120,
    max_frames: int = 0,
) -> None:
    """
    Launch the dashboard.

    `simulate=True` swaps the real scanner for `SimulatedBackend`, which needs
    no UTXO database - handy for trying the interface out or for screenshots.
    Any argument left as None falls back to `config.yaml`.
    """
    from mining_dark.config.settings import load_settings

    settings = settings or load_settings(config_path)
    bus = EventBus()

    if simulate:
        from mining_dark.gui.backends.simulated import SimulatedBackend
        backend: ScanBackend = SimulatedBackend(bus)
    else:
        from mining_dark.gui.backends.live import LiveBackend
        backend = LiveBackend(bus, settings)

    gui = MiningDarkGUI(
        backend,
        bus,
        settings=settings,
        config_path=config_path,
        palette_name=palette,
        language=language,
        font_scale=font_scale,
        autostart=autostart,
    )
    gui.run(screenshot=screenshot, screenshot_frames=screenshot_frames,
            max_frames=max_frames)
