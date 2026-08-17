"""Left panel: one progress row per scan worker."""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from mining_dark.i18n import t
from mining_dark.gui.panels.common import (
    PanelContext,
    group_thousands,
    panel_title,
    set_text,
    styled_text,
)
from mining_dark.gui.state import UIState, WorkerStatus
from mining_dark.gui.theme import status_color

_PANEL = "workers_panel"
_TABLE = "workers_table"
_SUBTITLE = "workers_subtitle"


class WorkersPanel:
    """
    Scrollable list of workers, each with a progress bar and a status label.

    Rows are created once and then updated in place; the table is only rebuilt
    when the worker count itself changes, which keeps the per-frame cost flat
    no matter how long the session runs.
    """

    def __init__(self, ctx: PanelContext) -> None:
        self.ctx = ctx
        self._row_count = 0

    # ----- construction ------------------------------------------------------
    def build(self, worker_count: int) -> None:
        ctx = self.ctx

        with dpg.child_window(tag=_PANEL, width=-1, height=-1, border=True) as panel:
            dpg.bind_item_theme(panel, ctx.themes.panel)
            panel_title(ctx, t("workers.title"), "", subtitle_tag=_SUBTITLE)

            with dpg.table(tag=_TABLE, header_row=False, borders_innerH=False,
                           borders_innerV=False, policy=dpg.mvTable_SizingStretchProp,
                           scrollY=False, freeze_rows=0):
                dpg.add_table_column(width_fixed=True, init_width_or_weight=36)
                dpg.add_table_column(init_width_or_weight=1.0)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=38)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=74)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=52)

            dpg.bind_item_theme(_TABLE, ctx.themes.table)

        self.rebuild(worker_count)

    def rebuild(self, worker_count: int) -> None:
        """Recreate the worker rows.  Called on build and on a worker-count change."""
        if worker_count == self._row_count or not dpg.does_item_exist(_TABLE):
            return

        for child in dpg.get_item_children(_TABLE, slot=1) or []:
            dpg.delete_item(child)

        ctx = self.ctx
        for i in range(worker_count):
            with dpg.table_row(parent=_TABLE):
                # Rows are numbered from one for reading; `worker_id` stays
                # zero-based everywhere else, since it indexes the list.
                styled_text(f"W{i + 1:02d}", color=ctx.palette.text_dim,
                            font=ctx.font("tiny"))

                # No overlay on the bar itself: light text over a neon fill is
                # unreadable at low contrast, so the percentage gets its own column.
                bar = dpg.add_progress_bar(default_value=0.0, width=-1, height=13,
                                           overlay="", tag=f"wk_bar_{i}")
                dpg.bind_item_theme(bar, ctx.themes.bar_waiting)

                styled_text("0%", color=ctx.palette.text_dim,
                            font=ctx.font("tiny"), tag=f"wk_pct_{i}")
                styled_text(WorkerStatus.WAITING.value, color=ctx.palette.text_faint,
                            font=ctx.font("tiny"), tag=f"wk_status_{i}")
                styled_text("0", color=ctx.palette.text_faint,
                            font=ctx.font("tiny"), tag=f"wk_count_{i}")

        self._row_count = worker_count

    # ----- per-frame ---------------------------------------------------------
    def update(self, state: UIState, now: float) -> None:
        if len(state.workers) != self._row_count:
            self.rebuild(len(state.workers))

        if not state.dirty_workers:
            return
        state.dirty_workers = False

        ctx = self.ctx
        active = 0

        # How far each worker is behind the busiest one.  See
        # `UIState.worker_shares` - this replaced a per-worker `progress` the
        # backend had to invent, a sawtooth that read the same on every row.
        shares = state.worker_shares()

        for i, (row, share) in enumerate(zip(state.workers, shares, strict=True)):
            status = row.status.value
            bar_tag = f"wk_bar_{i}"
            if not dpg.does_item_exist(bar_tag):
                continue

            dpg.set_value(bar_tag, share)
            dpg.bind_item_theme(bar_tag, ctx.themes.worker_bar(status))

            set_text(f"wk_pct_{i}", f"{share * 100:.0f}%")
            set_text(f"wk_status_{i}", status, status_color(status, ctx.palette))
            set_text(f"wk_count_{i}", group_thousands(row.checked))

            if row.status in (WorkerStatus.SCANNING, WorkerStatus.VERIFYING,
                              WorkerStatus.FOUND):
                active += 1

        set_text(_SUBTITLE, t("workers.active", active=active,
                      total=len(state.workers)))
