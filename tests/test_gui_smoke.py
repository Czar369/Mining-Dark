"""
The panels have to survive being drawn.

Every dialog update runs inside a try/except that logs once and carries on, so
a broken call site does not crash the app - it degrades in silence.  A node
section that raises `TypeError` on its first line still leaves a window that
opens, renders, and exits cleanly with nothing on stdout; the only trace is one
line in the log panel *inside* the window.  `mining-dark gui --simulate` cannot
see that, which is how a call missing a required argument shipped.

This is the check that does see it.

Dear PyGui allows a single context per process, and creating a second one
segfaults the interpreter - so the window is driven **once**, by a
session-scoped fixture, and every test below reads what that one run observed.
"""

from __future__ import annotations

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui")

#: Every settings tab, by its Portuguese label, in the order they are visited.
_TABS = ("Scanner", "Caminhos", "Node & UTXO", "Aparencia")

#: Frames spent on each tab.  Enough for the throttled updates to run at least
#: once - the node probe and the rebuild bar both gate on elapsed time.
_FRAMES_PER_TAB = 25

_TAB_PHASE_END = 3 + _FRAMES_PER_TAB * len(_TABS)


def _select_tab(label: str) -> bool:
    for item in dpg.get_all_items():
        if (dpg.get_item_type(item) == "mvAppItemType::mvTab"
                and dpg.get_item_label(item) == label):
            dpg.set_value(dpg.get_item_parent(item), item)
            return True
    return False


@pytest.fixture(scope="session")
def window_run(tmp_path_factory):
    """
    Open the real window once and record everything the tests need.

    Session-scoped on purpose: see the module docstring.
    """
    import os

    tmp_path = tmp_path_factory.mktemp("gui")
    os.environ["MINING_DARK_DATA_DIR"] = str(tmp_path / "data")

    import mining_dark.gui.app as gui_app
    from mining_dark.config.settings import Settings
    from mining_dark.gui.state import LogEvent, LogLevel

    settings = Settings()
    settings.ui.language = "pt"

    from mining_dark.utils.logger import setup_logger

    logs_dir = tmp_path / "logs"
    setup_logger(level="INFO", logs_dir=logs_dir)

    seen: dict = {
        "n": 0, "faults": set(), "visited": [], "missing": [],
        "logged": 0, "after_clear": None, "after_refilter": None,
        "logs_dir": logs_dir,
    }
    original = gui_app.MiningDarkGUI._frame

    def counted(self) -> None:
        seen["n"] += 1
        n = seen["n"]

        # --- phase 1: every settings tab gets drawn ---
        if n == 2:
            self.settings_dialog.open()
        index = (n - 3) // _FRAMES_PER_TAB
        if n >= 3 and (n - 3) % _FRAMES_PER_TAB == 0 and index < len(_TABS):
            seen["visited"].append(_TABS[index])
            if not _select_tab(_TABS[index]):
                seen["missing"].append(_TABS[index])

        # --- phase 2: clearing the log ---
        if n == _TAB_PHASE_END:
            self.settings_dialog.close()
            for i in range(8):
                self.bus.emit(LogEvent(LogLevel.ERROR, f"linha {i}"))

        original(self)
        seen["faults"] |= set(self._dialog_faults)

        if n == _TAB_PHASE_END + 4:
            seen["logged"] = len(self.state.logs)
            self.log_panel._on_clear()
        if n == _TAB_PHASE_END + 8:
            seen["after_clear"] = (len(self.log_panel._items), len(self.state.logs))
            self.log_panel._pending_refilter = True      # what the dropdown does
        if n == _TAB_PHASE_END + 12:
            seen["after_refilter"] = len(self.log_panel._items)
        if n >= _TAB_PHASE_END + 16:
            # Emitted on the last frame there will ever be, so nothing drains
            # it - only the closing drain in run()'s finally can persist it.
            self.bus.emit(LogEvent(LogLevel.INFO, "ultima linha da sessao"))
            dpg.stop_dearpygui()

    gui_app.MiningDarkGUI._frame = counted
    try:
        gui_app.run_gui(simulate=False, settings=settings)
    except Exception as exc:                       # pragma: no cover - no display
        pytest.skip(f"Dear PyGui cannot open a window here: {exc}")
    finally:
        gui_app.MiningDarkGUI._frame = original

    return seen


def test_every_settings_tab_renders_without_faulting(window_run) -> None:
    assert window_run["visited"] == list(_TABS)
    assert window_run["missing"] == [], "a settings tab was renamed and is not drawn"
    assert window_run["faults"] == set(), (
        "a panel raised while drawing; the app swallows this into its own log "
        "panel, so nothing else would have failed"
    )


def test_clearing_the_log_empties_the_screen_and_the_buffer(window_run) -> None:
    """
    The panel's own CLEAR used to wipe the rendered items only.

    That matters more since the footer's CLEAR LOG was removed: this is the
    only one left, so it has to do the whole job.
    """
    assert window_run["logged"] > 0, "no lines reached the state to clear"
    assert window_run["after_clear"] == (0, 0)


def test_a_filter_change_does_not_resurrect_cleared_lines(window_run) -> None:
    """
    `_refilter` rebuilds from `UIState.logs`.

    With a display-only clear, changing the level dropdown afterwards restored
    every line - eleven wiped, eleven back.
    """
    assert window_run["after_refilter"] == 0


def test_the_address_table_shrinks_as_the_font_grows() -> None:
    """
    A fixed row count grew with the font and squeezed the log below it.

    At scale 1.5 the log was down to three visible lines.  Dividing by the
    scale keeps the block's *height* roughly constant instead, so both halves
    stay usable; the floor keeps the table worth reading at any size.
    """
    from mining_dark.gui.panels.logs import _MIN_RECENT_ROWS, _RECENT_ROWS_AT_1X

    rows = {scale: max(_MIN_RECENT_ROWS, round(_RECENT_ROWS_AT_1X / scale))
            for scale in (1.0, 1.5, 2.5)}

    assert rows[1.0] > rows[1.5] > rows[2.5]
    assert rows[2.5] >= _MIN_RECENT_ROWS


def test_the_log_file_holds_the_end_of_the_session(window_run) -> None:
    """
    `_mirror_logs` runs once per frame, so the lines that close a session -
    emitted by `backend.stop()` in run()'s finally, after the last frame - had
    nowhere to go.  Closing the window instead of pressing STOP left the file
    ending mid-session.  A final drain after stop() is what catches them.
    """
    from loguru import logger

    logger.remove()          # flush the sinks this session's fixture opened

    written = "".join(
        path.read_text(encoding="utf-8")
        for path in window_run["logs_dir"].glob("scanner_*.log")
    )
    assert "Mining-Dark" in written, "the session never reached the log file"
    assert "ultima linha da sessao" in written
