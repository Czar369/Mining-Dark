"""
Right panel: the streaming terminal log plus the last verified addresses.

Log lines are real Dear PyGui text items so each severity can carry its own
colour.  To keep that affordable at scanner speeds the panel appends at most
`_MAX_APPEND_PER_FRAME` lines per frame and evicts from the top once the
buffer is full, so the item count is bounded no matter how long you run.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Callable, Optional

import dearpygui.dearpygui as dpg

from mining_dark.i18n import t
from mining_dark.gui.panels.common import (
    PanelContext,
    panel_title,
    set_text,
    styled_text,
)
from mining_dark.gui.state import LogLevel, UIState, fit_address
from mining_dark.gui.theme import log_color
from mining_dark.utils.logger import contains_secret, redact

_PANEL = "log_panel"
_LOG_CHILD = "log_child"
_SUBTITLE = "log_subtitle"
#: Rows in the addresses table at font scale 1.0.  Divided by the scale at
#: build time so the block keeps roughly the same *height* rather than the same
#: row count - a fixed count grew with the font and squeezed the log below it
#: down to three visible lines at scale 1.5.  The floor keeps it useful at any
#: size.  The panel is rebuilt whenever the scale changes (`_apply_font_scale`
#: -> `_apply_palette`), so this is re-evaluated then.
_RECENT_ROWS_AT_1X = 17
_MIN_RECENT_ROWS = 6

#: Width the TYPE column holds, at font scale 1.0.  `p2pkh_uncompressed` is the
#: longest label it has to fit.
_TYPE_COL_W = 118

#: Never abbreviate below this, however narrow the window gets - a stub with
#: more dots than address says nothing at all.
_MIN_ADDRESS_CHARS = 24
_MAX_ITEMS = 400
_MAX_APPEND_PER_FRAME = 60

_LEVEL_ORDER = {
    LogLevel.DEBUG: 0,
    LogLevel.INFO: 1,
    LogLevel.SUCCESS: 1,
    LogLevel.WARNING: 2,
    LogLevel.ERROR: 3,
}
# (translation key, minimum severity shown).  The combo displays the
# translated label; the threshold is looked up by index, so switching language
# cannot desynchronise the filter from its meaning.
_FILTERS = (
    ("log.filter.all", 0),
    ("log.filter.info", 1),
    ("log.filter.warn", 2),
    ("log.filter.error", 3),
)


class LogPanel:
    """Streaming log with severity colours, level filter and auto-scroll."""

    def __init__(self, ctx: PanelContext,
                 on_clear: Optional[Callable[[], None]] = None) -> None:
        self.ctx = ctx
        # Clearing has to reach `UIState.logs` too, not just the rendered
        # items: the buffer is what `_refilter` rebuilds from, so a display-only
        # clear was undone by the next change of the level dropdown - eleven
        # lines wiped, and eleven lines back.  The app owns both, so it does it.
        self._on_clear_requested = on_clear
        self._recent_rows = _RECENT_ROWS_AT_1X
        self._char_w = 6.7
        self._items: "deque[int | str]" = deque()
        self._auto_scroll = True
        self._threshold = 0
        self._pending_refilter = False

    # ----- construction ------------------------------------------------------
    def build(self) -> None:
        ctx = self.ctx

        with dpg.child_window(tag=_PANEL, width=-1, height=-1, border=True,
                              no_scrollbar=True) as panel:
            dpg.bind_item_theme(panel, ctx.themes.panel)

            # Recent addresses on top, the log underneath.  The addresses were
            # the squeezed one down here, clipped mid-row against the bottom
            # edge, while the log had height it did not need - a log you scroll
            # anyway loses nothing by being shorter.
            self._build_recent()

            dpg.add_spacer(height=6)
            panel_title(ctx, t("log.title"), "", subtitle_tag=_SUBTITLE)

            with dpg.group(horizontal=True):
                dpg.add_checkbox(label=t("log.autoscroll"), default_value=True,
                                 callback=self._on_auto_scroll)
                labels = [t(key) for key, _ in _FILTERS]
                dpg.add_combo(labels, default_value=labels[0], width=ctx.px(104),
                              callback=self._on_filter)
                clear = dpg.add_button(label=t("log.clear"), callback=self._on_clear,
                                       width=ctx.px(90))
                dpg.bind_item_theme(clear, ctx.themes.btn_ghost)

            with dpg.child_window(tag=_LOG_CHILD, width=-1, height=-1,
                                  border=True, horizontal_scrollbar=True) as log:
                dpg.bind_item_theme(log, ctx.themes.log_surface)

    def _build_recent(self) -> None:
        ctx = self.ctx
        scale = getattr(ctx.fonts, "scale", 1.0) or 1.0
        self._recent_rows = max(_MIN_RECENT_ROWS, round(_RECENT_ROWS_AT_1X / scale))
        panel_title(ctx, t("recent.title"), t("recent.subtitle"))

        with dpg.table(header_row=True, policy=dpg.mvTable_SizingStretchProp,
                       borders_innerH=False, borders_innerV=False,
                       row_background=True) as table:
            dpg.add_table_column(label=t("recent.col_type"), width_fixed=True,
                                 init_width_or_weight=118)
            dpg.add_table_column(label=t("recent.col_address"), init_width_or_weight=1.0)
            dpg.bind_item_theme(table, ctx.themes.table)

            for i in range(self._recent_rows):
                with dpg.table_row():
                    styled_text("-", color=ctx.palette.text_faint,
                                font=ctx.font("tiny"), tag=f"ra_type_{i}")
                    addr = styled_text("-", color=ctx.palette.text_faint,
                                       font=ctx.font("tiny"), tag=f"ra_addr_{i}")
                    # The exact value, always, for the rows too long to show
                    # whole - and for copying one off the screen by eye.
                    with dpg.tooltip(addr):
                        dpg.add_text("-", tag=f"ra_full_{i}")

        # Measured, not assumed: the panel's font is monospace, so one glyph
        # width scales to any address.  Taken here because a font-scale change
        # rebuilds the panel, which re-measures.
        sample = dpg.get_text_size("0" * 20, font=ctx.font("tiny"))
        self._char_w = (sample[0] / 20) if sample and sample[0] > 0 else 6.7 * scale

    # ----- callbacks ---------------------------------------------------------
    def _on_auto_scroll(self, _sender, value: bool) -> None:
        self._auto_scroll = bool(value)

    def _on_filter(self, _sender, label: str) -> None:
        for key, threshold in _FILTERS:
            if t(key) == label:
                self._threshold = threshold
                break
        self._pending_refilter = True

    def _on_clear(self, *_: object) -> None:
        if self._on_clear_requested is not None:
            # Clears the state buffer and calls `clear()` back on the way.
            self._on_clear_requested()
        else:
            self.clear()

    def clear(self) -> None:
        """Drop every rendered line.  The owning app also clears `UIState.logs`."""
        for item in self._items:
            if dpg.does_item_exist(item):
                dpg.delete_item(item)
        self._items.clear()
        set_text(_SUBTITLE, t("log.lines", count=0))

    # ----- per-frame ---------------------------------------------------------
    def update(self, state: UIState, now: float) -> None:
        if self._pending_refilter:
            self._pending_refilter = False
            self._refilter(state)

        self._append_pending(state)
        self._update_recent(state)

    def _append_pending(self, state: UIState) -> None:
        if not state.pending_logs:
            return

        batch = state.pending_logs[:_MAX_APPEND_PER_FRAME]
        del state.pending_logs[:_MAX_APPEND_PER_FRAME]

        threshold = self._threshold
        appended = 0

        for line in batch:
            if _LEVEL_ORDER.get(line.level, 1) < threshold:
                continue
            self._emit_line(line.level, line.message, line.ts)
            appended += 1

        if appended and self._auto_scroll and dpg.does_item_exist(_LOG_CHILD):
            dpg.set_y_scroll(_LOG_CHILD, -1.0)

        set_text(_SUBTITLE, t("log.lines", count=len(self._items)))

    def _emit_line(self, level: LogLevel, message: str, ts: float) -> None:
        """Render one line, redacting anything that smells like key material."""
        if contains_secret(message):
            message = redact(message)
            level = LogLevel.WARNING

        stamp = time.strftime("%H:%M:%S", time.localtime(ts))
        text = f"{stamp}  {level.value:<7} {message}"

        item = dpg.add_text(text, parent=_LOG_CHILD,
                            color=list(log_color(level.value, self.ctx.palette)))
        if self.ctx.fonts.tiny:
            dpg.bind_item_font(item, self.ctx.fonts.tiny)

        self._items.append(item)
        while len(self._items) > _MAX_ITEMS:
            old = self._items.popleft()
            if dpg.does_item_exist(old):
                dpg.delete_item(old)

    def _refilter(self, state: UIState) -> None:
        """Re-render the whole buffer after the level filter changed."""
        self.clear()
        threshold = self._threshold
        for line in list(state.logs)[-_MAX_ITEMS:]:
            if _LEVEL_ORDER.get(line.level, 1) >= threshold:
                self._emit_line(line.level, line.message, line.ts)
        if self._auto_scroll and dpg.does_item_exist(_LOG_CHILD):
            dpg.set_y_scroll(_LOG_CHILD, -1.0)
        set_text(_SUBTITLE, t("log.lines", count=len(self._items)))

    def _update_recent(self, state: UIState) -> None:
        if not state.dirty_recent:
            return
        state.dirty_recent = False

        p = self.ctx.palette
        entries = list(state.recent)[:self._recent_rows]
        budget = self._address_budget()

        for i in range(self._recent_rows):
            if i < len(entries):
                entry = entries[i]
                # Freshest row is brightest; older rows fade toward the background.
                color = p.accent if i == 0 else (p.text if i < 4 else p.text_dim)
                set_text(f"ra_type_{i}", entry.address_type, p.text_faint)
                set_text(f"ra_addr_{i}", fit_address(entry.address, budget), color)
                set_text(f"ra_full_{i}", entry.address)
            else:
                set_text(f"ra_type_{i}", "-", p.text_faint)
                set_text(f"ra_addr_{i}", "-", p.text_faint)
                set_text(f"ra_full_{i}", "-")

    def _address_budget(self) -> int:
        """
        Characters the ADDRESS column can show without being cut off.

        One rule for every format, so nothing is special-cased: whatever fits
        is printed whole, and only what genuinely cannot fit is shortened.  At
        the default width that means the 34- and 42-character formats appear
        complete and only the 62-character bech32 ones lose anything.

        Derived rather than fixed because both terms move: the panel is
        resizable, and at font scale 1.5 the same column holds barely two
        thirds of the characters.  A constant tuned at 1.0 silently overflowed,
        and Dear PyGui clips without an ellipsis - leaving no way to tell a
        whole address from a cut one.
        """
        panel_w = dpg.get_item_rect_size(_PANEL)[0] if dpg.does_item_exist(_PANEL) else 0
        # `ctx.px` already carries the font scale, so both terms scale together.
        # Panel padding plus the fixed TYPE column and the gap after it.
        available = panel_w - self.ctx.px(_TYPE_COL_W) - self.ctx.px(34)
        if available <= 0 or self._char_w <= 0:
            return _MIN_ADDRESS_CHARS
        return max(_MIN_ADDRESS_CHARS, int(available / self._char_w))
