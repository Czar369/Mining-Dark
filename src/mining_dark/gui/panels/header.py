"""Top strip: identity, session mode, live run-state badge and the two dialogs."""

from __future__ import annotations

import math

import dearpygui.dearpygui as dpg

from mining_dark.i18n import t
from mining_dark.gui.panels.common import PanelContext, set_text, styled_text
from mining_dark.gui.state import RunState, UIState

_STATE_KEY = {
    RunState.STOPPED: "state.stopped",
    RunState.STARTING: "state.starting",
    RunState.RUNNING: "state.running",
    RunState.PAUSED: "state.paused",
    RunState.STOPPING: "state.stopping",
    RunState.ERROR: "state.error",
}


class HeaderPanel:
    """Identity, session parameters, status badge, and the CONFIG / WALLETS buttons."""

    #: Height at font scale 1.0.  Two stacked rows of text live in here, so a
    #: fixed value made them overlap as soon as the font grew - use height().
    HEIGHT = 68

    @classmethod
    def height(cls, ctx) -> int:
        """Header height for the font size on screen."""
        return ctx.px(cls.HEIGHT)

    def __init__(self, ctx: PanelContext, on_open_settings=None,
                 on_open_wallets=None) -> None:
        self.ctx = ctx
        self._on_open_settings = on_open_settings
        self._on_open_wallets = on_open_wallets

    def build(self) -> None:
        ctx = self.ctx
        p = ctx.palette

        with dpg.child_window(tag="header_panel", height=self.height(ctx), border=True,
                              no_scrollbar=True) as panel:
            dpg.bind_item_theme(panel, ctx.themes.panel)

            with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                           borders_innerV=False):
                dpg.add_table_column(init_width_or_weight=0.34)
                dpg.add_table_column(init_width_or_weight=0.26)
                dpg.add_table_column(init_width_or_weight=0.22)
                dpg.add_table_column(init_width_or_weight=0.18)

                with dpg.table_row():
                    self._build_identity(p)
                    self._build_session(p)
                    self._build_status(p)
                    self._build_actions()

    # ----- cells -------------------------------------------------------------
    def _build_identity(self, p) -> None:
        ctx = self.ctx
        with dpg.group():
            with dpg.group(horizontal=True):
                styled_text("MINING", color=p.accent, font=ctx.font("h1"))
                styled_text("-DARK", color=p.text_dim, font=ctx.font("h1"))
            styled_text(t("app.tagline"), color=p.text_faint, font=ctx.font("tiny"))

    def _build_session(self, p) -> None:
        ctx = self.ctx
        with dpg.group():
            with dpg.group(horizontal=True):
                styled_text(t("header.mode"), color=p.text_faint, font=ctx.font("tiny"))
                styled_text("RANDOM", color=p.accent_bright, font=ctx.font("small"),
                            tag="hdr_mode")
            with dpg.group(horizontal=True):
                styled_text(t("header.workers"), color=p.text_faint, font=ctx.font("tiny"))
                styled_text("10", color=p.text, font=ctx.font("small"), tag="hdr_workers")
                styled_text("·", color=p.text_faint, font=ctx.font("small"))
                styled_text(t("header.backend"), color=p.text_faint, font=ctx.font("tiny"))
                styled_text("-", color=p.text, font=ctx.font("small"), tag="hdr_backend")

    def _build_status(self, p) -> None:
        ctx = self.ctx
        with dpg.group():
            with dpg.group(horizontal=True):
                styled_text("●", color=p.text_faint, font=ctx.font("h2"), tag="hdr_dot")
                styled_text(t(_STATE_KEY[RunState.STOPPED]), color=p.text_dim,
                            font=ctx.font("h2"), tag="hdr_state")
            # No wall clock beside it: the operating system already shows the
            # time of day, and two clocks side by side read as one figure split
            # in half.  What this strip is for is how long the program has run.
            with dpg.group(horizontal=True):
                styled_text(t("header.session"), color=p.text_faint, font=ctx.font("tiny"))
                styled_text("00:00:00", color=p.text, font=ctx.font("small"),
                            tag="hdr_session")

    def _build_actions(self) -> None:
        """
        CONFIG / WALLETS live here rather than behind a gear glyph: no monospace
        font ships a reliable gear, and a missing glyph renders as a tofu box on
        whichever machine lacks it.  Words always render.
        """
        ctx = self.ctx
        with dpg.group():
            settings = dpg.add_button(label=t("settings.title"), width=-1,
                                      tag="hdr_btn_settings",
                                      callback=lambda *_: self._open_settings())
            dpg.bind_item_theme(settings, ctx.themes.btn_ghost)
            with dpg.tooltip(settings):
                dpg.add_text(t("header.settings_tip"))

            wallets = dpg.add_button(label=t("wallets.title"), width=-1,
                                     tag="hdr_btn_wallets",
                                     callback=lambda *_: self._open_wallets())
            dpg.bind_item_theme(wallets, ctx.themes.btn_ghost)
            with dpg.tooltip(wallets):
                dpg.add_text(t("header.wallets_tip"))

    def _open_settings(self) -> None:
        if self._on_open_settings is not None:
            self._on_open_settings()

    def _open_wallets(self) -> None:
        if self._on_open_wallets is not None:
            self._on_open_wallets()

    # ----- per-frame ---------------------------------------------------------
    def update(self, state: UIState, now: float) -> None:
        p = self.ctx.palette

        color = {
            RunState.STOPPED: p.text_dim,
            RunState.STARTING: p.warning,
            RunState.RUNNING: p.accent,
            RunState.PAUSED: p.warning,
            RunState.STOPPING: p.warning,
            RunState.ERROR: p.error,
        }[state.run_state]

        set_text("hdr_state", t(_STATE_KEY[state.run_state]), color)

        # The dot breathes while running so a live session is obvious at a glance.
        if state.run_state is RunState.RUNNING:
            alpha = 140 + int(115 * (0.5 + 0.5 * math.sin(now * 4.0)))
            set_text("hdr_dot", "●", (color[0], color[1], color[2], alpha))
        else:
            set_text("hdr_dot", "●", color)

        set_text("hdr_session", state.session_hms())
