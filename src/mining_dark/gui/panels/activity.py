"""
Centre panel: radial activity graph and the stat tiles.

The graph is a radar: one node per worker sits on a ring, a sweep line rotates
around the hub, and nodes brighten both when their worker is busy and when the
sweep passes over them.  Geometry is rebuilt only when the panel is resized or
the worker count changes; per frame the panel just reconfigures colours and
positions, which keeps a 60 FPS redraw cheap.
"""

from __future__ import annotations

import math

import dearpygui.dearpygui as dpg

from mining_dark.i18n import t
from mining_dark.gui.panels.common import (
    PanelContext,
    group_thousands,
    human_rate,
    panel_title,
    set_text,
    stat_tile,
    styled_text,
)
from mining_dark.gui.state import RunState, UIState, WorkerStatus
from mining_dark.gui.theme import with_alpha

_PANEL = "activity_panel"
_DRAW = "activity_draw"
_SUBTITLE = "activity_subtitle"

#: Vertical space the stat tiles need below the graph, at font scale 1.0.
#: Everything it accounts for - six tiles and their captions - grows with the
#: font, so holding it fixed handed the extra height to the radar and pushed
#: the bottom row of tiles off the panel.  Was 258 while three phase bars sat
#: between the two; they are gone and the radar takes what they held.
_RESERVED_HEIGHT = 200
_MIN_GRAPH_HEIGHT = 130

class ActivityPanel:
    """Radar graph + real-time statistics."""

    def __init__(self, ctx: PanelContext, *, simulated: bool = False) -> None:
        self.ctx = ctx
        self.simulated = simulated
        self._geometry: tuple[int, int, int] = (0, 0, 0)   # (width, height, nodes)
        self._center = (0.0, 0.0)
        self._radius = 0.0
        self._node_pos: list[tuple[float, float]] = []
        self._found_flash_until = 0.0
        # None until the first frame: a panel rebuilt mid-session (theme or
        # language switch) must adopt the current count silently instead of
        # flashing as if every wallet had just been found.
        self._last_found_count: int | None = None

    # ----- construction ------------------------------------------------------
    def build(self) -> None:
        ctx = self.ctx

        # Scrollable rather than clipped: at a large font scale the radar and
        # the six tiles need more height than the window has, and silently
        # hiding the bottom row of numbers is worse than a scrollbar.
        with dpg.child_window(tag=_PANEL, width=-1, height=-1, border=True) as panel:
            dpg.bind_item_theme(panel, ctx.themes.panel)
            panel_title(ctx, t("activity.title"), "", subtitle_tag=_SUBTITLE)

            dpg.add_drawlist(tag=_DRAW, width=ctx.px(320), height=_MIN_GRAPH_HEIGHT)

            # No phase bars between the radar and the tiles.  They showed
            # throughput against the session's best, which moved far too fast
            # to read - the numbers in the tiles below say the same thing
            # steadily, and the space goes to the radar instead.
            dpg.add_spacer(height=3)
            self._build_tiles()

    def _build_tiles(self) -> None:
        ctx = self.ctx
        # The two tiles a person would screenshot and believe.  Marked when the
        # numbers behind them are invented, so a simulated run cannot be
        # mistaken for a real find.
        sim = f"  {t('tile.simulated')}" if self.simulated else ""
        rows = (
            ((t("tile.kps"), "st_kps", ctx.palette.accent),
             (t("tile.cps"), "st_cps", ctx.palette.info),
             (t("tile.found") + sim, "st_found", ctx.palette.found)),
            ((t("tile.keys"), "st_keys", ctx.palette.text),
             (t("tile.addresses"), "st_addr", ctx.palette.text),
             (t("tile.btc") + sim, "st_btc", ctx.palette.accent_bright)),
        )

        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp,
                       borders_innerV=False) as table:
            for _ in range(3):
                dpg.add_table_column(init_width_or_weight=1.0)
            dpg.bind_item_theme(table, ctx.themes.table)

            for row in rows:
                with dpg.table_row():
                    for label, tag, color in row:
                        stat_tile(ctx, label, tag, "0", height=ctx.px(56), value_color=color)

    # ----- geometry ----------------------------------------------------------
    def _ensure_geometry(self, node_count: int) -> None:
        """Resize the drawlist to the panel and rebuild the radar if needed."""
        if not dpg.does_item_exist(_PANEL) or not dpg.does_item_exist(_DRAW):
            return

        ctx = self.ctx
        panel_w, panel_h = dpg.get_item_rect_size(_PANEL)
        width = max(ctx.px(220), int(panel_w) - ctx.px(22))
        # The floor deliberately does not scale: the radar is decoration and
        # the tiles are the data, so when the two compete for a window that
        # cannot hold both, the radar is what gives way.
        height = max(_MIN_GRAPH_HEIGHT, int(panel_h) - ctx.px(_RESERVED_HEIGHT))

        if (width, height, node_count) == self._geometry:
            return

        self._geometry = (width, height, node_count)
        dpg.configure_item(_DRAW, width=width, height=height)
        self._rebuild_radar(width, height, node_count)

    def _rebuild_radar(self, width: int, height: int, node_count: int) -> None:
        ctx = self.ctx
        p = ctx.palette

        for child in dpg.get_item_children(_DRAW, slot=2) or []:
            dpg.delete_item(child)

        cx, cy = width / 2.0, height / 2.0
        radius = max(40.0, min(cx, cy) - 16.0)
        self._center = (cx, cy)
        self._radius = radius

        # ----- static chrome: rings, grid, crosshair -------------------------
        for factor in (1.0, 0.72, 0.44, 0.18):
            dpg.draw_circle((cx, cy), radius * factor, parent=_DRAW,
                            color=with_alpha(p.grid, 150 if factor == 1.0 else 90),
                            thickness=1.0, segments=72)

        for step in range(12):
            angle = step * math.tau / 12
            dpg.draw_line(
                (cx + radius * 0.18 * math.cos(angle), cy + radius * 0.18 * math.sin(angle)),
                (cx + radius * math.cos(angle), cy + radius * math.sin(angle)),
                parent=_DRAW, color=with_alpha(p.grid, 55), thickness=1.0,
            )

        # ----- sweep wedge + leading edge ------------------------------------
        dpg.draw_polygon([(cx, cy), (cx, cy), (cx, cy)], parent=_DRAW, tag="g_wedge",
                         color=(0, 0, 0, 0), fill=with_alpha(p.accent, 26),
                         thickness=0.0)
        dpg.draw_line((cx, cy), (cx + radius, cy), parent=_DRAW, tag="g_sweep",
                      color=with_alpha(p.accent, 170), thickness=1.6)

        # ----- one spoke + node per worker -----------------------------------
        self._node_pos = []
        for i in range(node_count):
            angle = -math.pi / 2 + i * math.tau / max(1, node_count)
            nx, ny = cx + radius * 0.82 * math.cos(angle), cy + radius * 0.82 * math.sin(angle)
            self._node_pos.append((nx, ny))

            dpg.draw_line((cx, cy), (nx, ny), parent=_DRAW, tag=f"g_spoke_{i}",
                          color=with_alpha(p.accent, 34), thickness=1.0)
            dpg.draw_circle((nx, ny), 9.0, parent=_DRAW, tag=f"g_ring_{i}",
                            color=with_alpha(p.accent, 60), thickness=1.0, segments=18)
            dpg.draw_circle((nx, ny), 4.0, parent=_DRAW, tag=f"g_node_{i}",
                            color=with_alpha(p.accent, 90),
                            fill=with_alpha(p.accent, 60), thickness=1.0, segments=16)

        # ----- hub -----------------------------------------------------------
        dpg.draw_circle((cx, cy), radius * 0.18, parent=_DRAW, tag="g_hub",
                        color=with_alpha(p.accent, 190),
                        fill=with_alpha(p.bg_deep, 235), thickness=1.4, segments=48)
        dpg.draw_text((cx - 26, cy - 15), "0", parent=_DRAW, tag="g_hub_value",
                      color=p.accent, size=18)
        dpg.draw_text((cx - 26, cy + 4), t("activity.hub_label"), parent=_DRAW,
                      tag="g_hub_label",
                      color=p.text_faint, size=11)

    # ----- per-frame ---------------------------------------------------------
    def update(self, state: UIState, now: float) -> None:
        self._ensure_geometry(len(state.workers))
        self._update_radar(state, now)
        self._update_tiles(state)

    def _update_radar(self, state: UIState, now: float) -> None:
        if not self._node_pos or not dpg.does_item_exist("g_sweep"):
            return

        ctx = self.ctx
        p = ctx.palette
        cx, cy = self._center
        radius = self._radius

        # Radar sweep - one revolution every ~7 s.  It freezes whenever the
        # scan is not actually producing work, pause included.
        scanning = state.run_state is RunState.RUNNING
        sweep = (now * 0.9) % math.tau if scanning else -math.pi / 2
        dpg.configure_item("g_sweep", p2=(cx + radius * math.cos(sweep),
                                          cy + radius * math.sin(sweep)))
        wedge = [(cx, cy)]
        for k in range(6):
            a = sweep - 0.42 * k / 5
            wedge.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
        dpg.configure_item("g_wedge", points=wedge)

        # Found flash - the whole hub pulses magenta for a moment after a hit.
        if self._last_found_count is None:
            self._last_found_count = len(state.found)
        elif len(state.found) != self._last_found_count:
            self._last_found_count = len(state.found)
            self._found_flash_until = now + 1.6
        flashing = now < self._found_flash_until

        for i, (nx, ny) in enumerate(self._node_pos):
            if i >= len(state.workers):
                break
            worker = state.workers[i]

            base = {
                WorkerStatus.WAITING: p.accent_soft,
                WorkerStatus.SCANNING: p.accent,
                WorkerStatus.VERIFYING: p.info,
                WorkerStatus.FOUND: p.found,
                WorkerStatus.STOPPED: p.text_faint,
            }[worker.status]

            busy = worker.status in (WorkerStatus.SCANNING, WorkerStatus.VERIFYING,
                                     WorkerStatus.FOUND)

            # Brightness = own activity, plus a boost while the sweep is on top.
            node_angle = math.atan2(ny - cy, nx - cx) % math.tau
            delta = abs((sweep - node_angle + math.pi) % math.tau - math.pi)
            sweep_boost = max(0.0, 1.0 - delta / 0.9)

            pulse = 0.5 + 0.5 * math.sin(now * 5.0 + i * 0.7) if busy else 0.0
            energy = min(1.0, 0.16 + 0.5 * pulse + 0.55 * sweep_boost) if busy \
                else 0.10 + 0.35 * sweep_boost

            # Radius comes from `energy` alone.  It used to add
            # `3.0 * worker.progress`, and that progress was a number the live
            # backend invented - so the one part of the radar that looked like
            # a measurement was the only part that was not one.
            node_radius = 3.4 + 4.6 * energy

            dpg.configure_item(f"g_node_{i}",
                               radius=node_radius,
                               color=with_alpha(base, int(90 + 165 * energy)),
                               fill=with_alpha(base, int(40 + 150 * energy)))
            dpg.configure_item(f"g_ring_{i}",
                               radius=9.0 + 5.0 * energy,
                               color=with_alpha(base, int(30 + 140 * energy)))
            dpg.configure_item(f"g_spoke_{i}",
                               color=with_alpha(base, int(22 + 110 * energy)))

        hub_color = p.found if flashing else p.accent
        hub_alpha = 255 if flashing else 190
        dpg.configure_item("g_hub", color=with_alpha(hub_color, hub_alpha),
                           radius=radius * (0.18 + (0.03 if flashing else 0.0)))

        rate = human_rate(state.stats.keys_per_second)
        dpg.configure_item("g_hub_value", text=rate,
                           pos=(cx - 4.5 * len(rate), cy - 16),
                           color=hub_color)
        dpg.configure_item("g_hub_label", pos=(cx - 26, cy + 4))

        set_text(_SUBTITLE, t(
            "activity.subtitle",
            nodes=len(self._node_pos),
            sweep=t("activity.sweep_on") if scanning else t("activity.sweep_off"),
        ))

    def _update_tiles(self, state: UIState) -> None:
        s = state.stats
        set_text("st_kps", human_rate(s.keys_per_second))
        set_text("st_cps", human_rate(s.checks_per_second))
        set_text("st_found", str(s.wallets_found))
        set_text("st_keys", group_thousands(s.keys_generated))
        set_text("st_addr", group_thousands(s.addresses_checked))
        set_text("st_btc", f"{state.total_btc_found:.4f}")
