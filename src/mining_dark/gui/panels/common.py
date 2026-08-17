"""Shared scaffolding for the dashboard panels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import dearpygui.dearpygui as dpg

from mining_dark.gui.theme import Fonts, Palette, Themes

#: Accent bar drawn before every panel title.
ACCENT_BAR = "▌"      # ▌
#: Horizontal rule character used inside the log header.
RULE = "─"            # ─


@dataclass(frozen=True, slots=True)
class PanelContext:
    """Everything a panel needs to style itself.  Passed down from the app."""

    palette: Palette
    themes: Themes
    fonts: Fonts

    def font(self, name: str) -> int:
        return getattr(self.fonts, name, 0)

    def px(self, value: int) -> int:
        """
        A size authored at font scale 1.0, converted to the scale on screen.

        Widget sizes are fixed pixels while the text inside them is not, so at
        scale 2.0 labels outgrew their buttons and were clipped mid-word -
        "WALLETS ENCONTRADAS" rendered as "WALLETS ENCONTRADA", and the footer
        buttons overlapped each other.
        """
        scale = getattr(self.fonts, "scale", 1.0) or 1.0
        return int(value * scale)


def styled_text(text: str, *, color=None, font: int = 0, tag: int | str = 0,
                wrap: int = -1) -> int | str:
    """`add_text` plus optional font binding, returning the item tag."""
    kwargs = {"wrap": wrap}
    if color is not None:
        kwargs["color"] = list(color)
    if tag:
        kwargs["tag"] = tag
    item = dpg.add_text(text, **kwargs)
    if font:
        dpg.bind_item_font(item, font)
    return item


def panel_title(ctx: PanelContext, title: str, subtitle: str = "",
                subtitle_tag: int | str = 0) -> int | str:
    """
    Standard panel header:  ▌ TITLE   subtitle

    Returns the subtitle item tag so callers can update it every frame.
    """
    handle: int | str = 0
    with dpg.group(horizontal=True):
        styled_text(ACCENT_BAR, color=ctx.palette.accent, font=ctx.font("h2"))
        styled_text(title, color=ctx.palette.accent, font=ctx.font("h2"))
        if subtitle or subtitle_tag:
            handle = styled_text(subtitle, color=ctx.palette.text_faint,
                                 font=ctx.font("small"), tag=subtitle_tag)
    dpg.add_separator()
    return handle


def key_value_row(ctx: PanelContext, label: str, value: str, *,
                  value_tag: int | str = 0, value_color=None,
                  label_chars: int = 18) -> int | str:
    """
    A dim label followed by a value.

    Alignment comes from padding the label, not from `set_item_width` - text
    items carry no width in Dear PyGui 2.x, and the UI is monospace anyway.
    """
    with dpg.group(horizontal=True):
        styled_text(label.ljust(label_chars), color=ctx.palette.text_dim,
                    font=ctx.font("small"))
        return styled_text(value, color=value_color or ctx.palette.text,
                           font=ctx.font("small"), tag=value_tag)


def stat_tile(ctx: PanelContext, label: str, value_tag: str, initial: str = "0",
              *, width: int = -1, height: int = 58, value_color=None) -> None:
    """A bordered tile with a small caption over a large monospace number."""
    with dpg.child_window(width=width, height=height, border=True,
                          no_scrollbar=True) as tile:
        dpg.bind_item_theme(tile, ctx.themes.tile)
        styled_text(label.upper(), color=ctx.palette.text_dim, font=ctx.font("tiny"))
        styled_text(initial, color=value_color or ctx.palette.accent,
                    font=ctx.font("metric"), tag=value_tag)


def set_text(tag: str, value: str, color=None) -> None:
    """Update a text item, tolerating tags that were deleted mid-rebuild."""
    if not dpg.does_item_exist(tag):
        return
    dpg.set_value(tag, value)
    if color is not None:
        dpg.configure_item(tag, color=list(color))


def human_count(value: int) -> str:
    """Compact large counters: 1234 -> '1.23K', 4500000 -> '4.50M'."""
    if value < 1_000:
        return str(value)
    if value < 1_000_000:
        return f"{value / 1_000:.2f}K"
    if value < 1_000_000_000:
        return f"{value / 1_000_000:.2f}M"
    return f"{value / 1_000_000_000:.2f}G"


def human_rate(value: float) -> str:
    """Rates get one decimal below 1000, then the compact form."""
    if value < 1_000:
        return f"{value:.1f}"
    return human_count(int(value))


def group_thousands(value: int) -> str:
    """
    1234567 -> '1,234,567'.

    US grouping in both languages, deliberately.  Everything this panel sits
    beside writes numbers that way - the CLI, bitcoin-cli, debug.log, every
    block explorer - so translating them here only made the same figure look
    different depending on where you read it.

    It also removes a whole class of bug: the pt-BR form was produced by
    swapping "," for "." after the fact, which is correct for integers and
    silently wrong the moment a decimal point is involved (1,234.5 became
    "1.234.5", where the last separator claims to be another thousands group).
    """
    return f"{value:,}"


def optional_tooltip(item: int | str, text: Optional[str]) -> None:
    if text:
        with dpg.tooltip(item):
            dpg.add_text(text)


#: Height a modal reserves below its body for the separator and button row.
MODAL_FOOTER_H = 46


#: Below this the viewport reading is treated as garbage rather than as a tiny
#: window.  The value can come back stale (or zero) on the frame right after a
#: rebuild, and shrinking a dialog to match would leave it stuck small.
_MIN_SANE_VIEWPORT = 400


def fit_and_center(tag: str, *, max_width: int, max_height: int,
                   margin: int = 40) -> None:
    """
    Size a modal to fit the viewport, then centre it.

    Without the clamp a dialog taller than the window would push its own action
    buttons off-screen on a small display.  With a naive clamp, a bogus
    viewport reading would do the same in reverse - hence the sanity floor: an
    implausible reading leaves the dialog at its declared size.
    """
    if not dpg.does_item_exist(tag):
        return
    try:
        vw = dpg.get_viewport_client_width()
        vh = dpg.get_viewport_client_height()
    except SystemError:                    # pragma: no cover - viewport not up yet
        return

    if vw < _MIN_SANE_VIEWPORT or vh < _MIN_SANE_VIEWPORT:
        dpg.configure_item(tag, width=max_width, height=max_height)
        return

    width = min(max_width, vw - margin * 2)
    height = min(max_height, vh - margin * 2)

    dpg.configure_item(tag, width=width, height=height)
    dpg.set_item_pos(tag, [max(0, (vw - width) // 2), max(0, (vh - height) // 2)])
