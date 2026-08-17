"""Bottom strip: transport controls, quick settings and the system status line."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import dearpygui.dearpygui as dpg

from mining_dark.i18n import t
from mining_dark.gui.panels.common import (
    PanelContext,
    group_thousands,
    set_text,
    styled_text,
)
from mining_dark.gui.state import DBStatus, RunState, UIState

_DB_KEY = {
    DBStatus.UNKNOWN: "db.unknown",
    DBStatus.MISSING: "db.missing",
    DBStatus.OUTDATED: "db.outdated",
    DBStatus.OK: "db.ok",
    DBStatus.SIMULATED: "db.simulated",
}


@dataclass(frozen=True, slots=True)
class FooterCallbacks:
    """
    Wiring from the footer widgets back to the application.

    Scanner mode, worker count and palette live in the settings dialog only -
    they were duplicated here as instant-apply controls, which meant two places
    to keep in sync for values the user changes rarely.
    """

    on_start: Callable[[], None]
    on_pause: Callable[[], None]
    on_stop: Callable[[], None]


class FooterPanel:
    """Transport controls plus a one-line health readout of the UTXO database."""

    #: Height at font scale 1.0.  See HeaderPanel.height().
    HEIGHT = 84

    @classmethod
    def height(cls, ctx) -> int:
        """Footer height for the font size on screen."""
        return ctx.px(cls.HEIGHT)

    def __init__(self, ctx: PanelContext, callbacks: FooterCallbacks) -> None:
        self.ctx = ctx
        self.cb = callbacks

    # ----- construction ------------------------------------------------------
    def build(self) -> None:
        ctx = self.ctx
        p = ctx.palette

        with dpg.child_window(height=self.height(ctx), border=True,
                              no_scrollbar=True) as panel:
            dpg.bind_item_theme(panel, ctx.themes.panel)

            # ----- controls --------------------------------------------------
            with dpg.group(horizontal=True):
                start = dpg.add_button(label=t("btn.start"), width=ctx.px(104), tag="btn_start",
                                       callback=lambda *_: self.cb.on_start())
                dpg.bind_item_theme(start, ctx.themes.btn_start)

                pause = dpg.add_button(label=t("btn.pause"), width=ctx.px(104), tag="btn_pause",
                                       callback=lambda *_: self.cb.on_pause())
                dpg.bind_item_theme(pause, ctx.themes.btn_pause)

                stop = dpg.add_button(label=t("btn.stop"), width=ctx.px(104), tag="btn_stop",
                                      callback=lambda *_: self.cb.on_stop())
                dpg.bind_item_theme(stop, ctx.themes.btn_stop)


            dpg.add_separator()

            # ----- status strip ----------------------------------------------
            with dpg.group(horizontal=True):
                styled_text(t("footer.db"), color=p.text_faint, font=ctx.font("tiny"))
                styled_text("●", color=p.text_faint, font=ctx.font("small"),
                            tag="ft_db_dot")
                styled_text(t("db.unknown"), color=p.text_dim, font=ctx.font("small"),
                            tag="ft_db_status")
                styled_text("│", color=p.border, font=ctx.font("small"))
                styled_text(t("footer.addresses"), color=p.text_faint, font=ctx.font("tiny"))
                styled_text("-", color=p.text, font=ctx.font("small"),
                            tag="ft_db_count")
                styled_text("│", color=p.border, font=ctx.font("small"))
                styled_text(t("footer.size"), color=p.text_faint, font=ctx.font("tiny"))
                styled_text("-", color=p.text, font=ctx.font("small"),
                            tag="ft_db_size")
                styled_text("│", color=p.border, font=ctx.font("small"))
                styled_text(t("footer.updated"), color=p.text_faint, font=ctx.font("tiny"))
                styled_text("-", color=p.text, font=ctx.font("small"),
                            tag="ft_db_updated")
                styled_text("│", color=p.border, font=ctx.font("small"))
                styled_text(t("footer.source"), color=p.text_faint, font=ctx.font("tiny"))
                styled_text("-", color=p.text_dim, font=ctx.font("small"),
                            tag="ft_db_source")
                styled_text("│", color=p.border, font=ctx.font("small"))
                styled_text("", color=p.text_dim, font=ctx.font("small"),
                            tag="ft_notice")

    # ----- per-frame ---------------------------------------------------------
    def update(self, state: UIState, now: float) -> None:
        p = self.ctx.palette
        running = state.run_state in (RunState.RUNNING, RunState.PAUSED,
                                      RunState.STARTING)

        dpg.configure_item("btn_start", enabled=not running)
        dpg.configure_item("btn_pause", enabled=running,
                           label=t("btn.resume")
                           if state.run_state is RunState.PAUSED else t("btn.pause"))
        dpg.configure_item("btn_stop", enabled=running)

        db = state.database
        color = {
            DBStatus.OK: p.accent,
            DBStatus.SIMULATED: p.info,
            DBStatus.OUTDATED: p.warning,
            DBStatus.MISSING: p.error,
            DBStatus.UNKNOWN: p.text_dim,
        }[db.status]

        set_text("ft_db_dot", "●", color)
        set_text("ft_db_status", t(_DB_KEY[db.status]), color)
        set_text("ft_db_count", group_thousands(db.address_count) if db.address_count
                 else "-")
        set_text("ft_db_size", f"{db.size_mb:,.1f} MB"
                 if db.size_mb else "-")
        set_text("ft_db_updated", db.last_updated or "-")
        set_text("ft_db_source", db.source or "-")

        if db.status is DBStatus.MISSING:
            set_text("ft_notice", t("footer.db_hint"), p.warning)
        elif state.run_detail:
            set_text("ft_notice", state.run_detail, p.error)
        else:
            set_text("ft_notice", "", p.text_dim)
