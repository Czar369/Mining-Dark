"""
Cyberpunk / HUD look for the Dear PyGui front-end.

Everything visual lives here: the colour palettes, the monospace font ladder
and every Dear PyGui theme object the panels bind to.  Panels ask this module
for a colour or a theme tag - they never hard-code an RGB triple.

The accent colour is swappable at runtime (`PALETTES`), which is what makes the
"tema configurável" requirement cheap: rebuild the themes, rebind, done.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

import dearpygui.dearpygui as dpg

RGBA = tuple[int, int, int, int]


# ═══════════════════════════════════════════════════════════════════════════════
#  Palettes
# ═══════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True, slots=True)
class Palette:
    """A complete colour scheme.  Only the accent family changes between presets."""

    name: str
    label: str

    # Surfaces - near-black, three elevation levels
    bg_deep: RGBA = (10, 10, 15, 255)        # #0A0A0F  viewport / root window
    bg_panel: RGBA = (13, 17, 23, 255)       # #0D1117  panel child windows
    bg_elev: RGBA = (18, 24, 32, 255)        # #121820  frames, table rows
    bg_input: RGBA = (8, 12, 16, 255)        # inputs, log surface

    # Accent family (the neon)
    accent: RGBA = (0, 255, 65, 255)         # #00FF41
    accent_bright: RGBA = (57, 255, 20, 255)  # #39FF14
    accent_dim: RGBA = (0, 165, 45, 255)
    accent_soft: RGBA = (0, 96, 30, 255)
    accent_ghost: RGBA = (0, 255, 65, 38)    # translucent fills / glows

    # Chrome
    border: RGBA = (0, 78, 30, 255)
    border_hot: RGBA = (0, 255, 65, 190)
    grid: RGBA = (0, 60, 24, 255)

    # Text
    text: RGBA = (196, 232, 202, 255)
    text_dim: RGBA = (108, 140, 116, 255)
    text_faint: RGBA = (62, 84, 68, 255)

    # Semantic (stable across presets so log severity always reads the same)
    info: RGBA = (0, 208, 255, 255)
    success: RGBA = (57, 255, 20, 255)
    warning: RGBA = (255, 176, 0, 255)
    error: RGBA = (255, 72, 72, 255)
    found: RGBA = (255, 64, 200, 255)


PALETTES: dict[str, Palette] = {
    "matrix": Palette(name="matrix", label="Matrix Green"),
    "amber": Palette(
        name="amber",
        label="Amber Terminal",
        accent=(255, 176, 0, 255),
        accent_bright=(255, 214, 76, 255),
        accent_dim=(178, 120, 0, 255),
        accent_soft=(96, 64, 0, 255),
        accent_ghost=(255, 176, 0, 38),
        border=(92, 62, 0, 255),
        border_hot=(255, 176, 0, 190),
        grid=(70, 48, 0, 255),
        text=(236, 218, 182, 255),
        text_dim=(150, 126, 84, 255),
        text_faint=(92, 76, 48, 255),
    ),
    "ice": Palette(
        name="ice",
        label="Ice Cyan",
        accent=(0, 224, 255, 255),
        accent_bright=(120, 244, 255, 255),
        accent_dim=(0, 150, 180, 255),
        accent_soft=(0, 84, 104, 255),
        accent_ghost=(0, 224, 255, 38),
        border=(0, 74, 92, 255),
        border_hot=(0, 224, 255, 190),
        grid=(0, 56, 70, 255),
        text=(198, 232, 240, 255),
        text_dim=(104, 146, 160, 255),
        text_faint=(58, 84, 94, 255),
        # The default `info` cyan collides with this accent, which would make
        # SCANNING and VERIFYING indistinguishable - shift it to periwinkle.
        info=(150, 132, 255, 255),
    ),
}

DEFAULT_PALETTE = "matrix"


# ═══════════════════════════════════════════════════════════════════════════════
#  Fonts
# ═══════════════════════════════════════════════════════════════════════════════
# Static (non-variable) monospace TTFs, best first.  Variable fonts such as
# Ubuntu's `UbuntuMono[wght].ttf` are skipped - stb_truetype renders them at the
# wrong weight.
_FONT_CANDIDATES: tuple[str, ...] = (
    "JetBrainsMono-Regular.ttf",
    "JetBrainsMonoNL-Regular.ttf",
    "FiraCode-Regular.ttf",
    "FiraMono-Regular.ttf",
    "Hack-Regular.ttf",
    "SourceCodePro-Regular.ttf",
    "CascadiaMono-Regular.ttf",
    "DejaVuSansMono.ttf",
    "LiberationMono-Regular.ttf",
    "NotoSansMono-Regular.ttf",
)

_FONT_DIRS: tuple[Path, ...] = (
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".local/share/fonts",
    Path.home() / ".fonts",
    Path("/Library/Fonts"),
    Path("/System/Library/Fonts"),
    Path("C:/Windows/Fonts"),
)


def find_mono_font() -> Optional[Path]:
    """Locate the best available monospace TTF, or None to use the DPG default."""
    for candidate in _FONT_CANDIDATES:
        for root in _FONT_DIRS:
            if not root.is_dir():
                continue
            try:
                match = next(root.rglob(candidate), None)
            except OSError:                      # unreadable font dir - skip it
                continue
            if match is not None:
                return match
    return None


@dataclass(slots=True)
class Fonts:
    """Font ladder.  A value of 0 means "no custom font, use the DPG default"."""

    tiny: int = 0      # 11px - table cells, timestamps
    small: int = 0     # 12px - secondary labels
    body: int = 0      # 14px - default UI text
    h2: int = 0        # 16px - panel titles
    h1: int = 0        # 22px - product title
    metric: int = 0    # 28px - big stat numbers
    source: Optional[Path] = None
    #: The multiplier currently on screen.  Layout that has to follow the text
    #: measures against this, never against a pending config value.
    scale: float = 1.0
    #: The multiplier the atlas was actually baked at.  Dear PyGui bakes it once
    #: at setup_dearpygui(), so a scale change afterwards is served by
    #: `set_global_font_scale` on top of these glyphs - crisp again only after a
    #: restart, but visible immediately.
    baked_scale: float = 1.0


def _needs_explicit_ranges() -> bool:
    """
    Dear PyGui 1.x only bakes the glyphs you ask for; 2.x derives the ranges
    automatically and deprecates the calls.  Ask before declaring them.
    """
    try:
        return int(dpg.get_dearpygui_version().split(".")[0]) < 2
    except (ValueError, IndexError, SystemError):
        return False


def build_fonts(scale: float = 1.0) -> Fonts:
    """
    Register the font ladder.  Must run after `create_context()` and *before*
    `setup_dearpygui()`, because that is when Dear PyGui bakes the font atlas.
    """
    path = find_mono_font()
    if path is None:
        return Fonts(scale=scale, baked_scale=scale)

    fonts = Fonts(source=path, scale=scale, baked_scale=scale)
    sizes = {"tiny": 11, "small": 12, "body": 14, "h2": 16, "h1": 22, "metric": 28}
    declare_ranges = _needs_explicit_ranges()

    with dpg.font_registry():
        for attr, size in sizes.items():
            with dpg.font(str(path), int(round(size * scale))) as handle:
                if declare_ranges:
                    # Latin-1 keeps Portuguese accents (ç, ã, é) legible, and
                    # 0x2500-0x259F carries the HUD rules and accent bars.
                    dpg.add_font_range(0x0020, 0x00FF)
                    dpg.add_font_range(0x2500, 0x259F)
                    dpg.add_font_range_hint(dpg.mvFontRangeHint_Default)
            setattr(fonts, attr, handle)

    return fonts


# ═══════════════════════════════════════════════════════════════════════════════
#  Theme construction helpers
# ═══════════════════════════════════════════════════════════════════════════════
def _color(name: str, value: Sequence[int], category: int = 0) -> None:
    """
    Add a theme colour, silently skipping constants this Dear PyGui build does
    not expose.  ImGui renames enum members between releases (TabActive ->
    TabSelected, for one), and a missing accent is not worth a crash.
    """
    target = getattr(dpg, f"mvThemeCol_{name}", None)
    if target is not None:
        dpg.add_theme_color(target, list(value), category=category or dpg.mvThemeCat_Core)


def _style(name: str, *values: float) -> None:
    """Add a style var, skipping unknown constants (see `_color`)."""
    target = getattr(dpg, f"mvStyleVar_{name}", None)
    if target is not None:
        dpg.add_theme_style(target, *values, category=dpg.mvThemeCat_Core)


@dataclass(slots=True)
class Themes:
    """Every theme tag the panels bind to.  Built once per palette."""

    palette: Palette

    global_theme: int = 0
    panel: int = 0
    plain_panel: int = 0        # child window with no border/padding (drawlists)
    log_surface: int = 0

    btn_start: int = 0
    btn_pause: int = 0
    btn_stop: int = 0
    btn_ghost: int = 0

    bar_waiting: int = 0
    bar_scanning: int = 0
    bar_verifying: int = 0
    bar_found: int = 0
    bar_stopped: int = 0
    bar_scan_phase: int = 0
    bar_verify_phase: int = 0
    bar_merge_phase: int = 0

    tile: int = 0               # stat tile container
    table: int = 0

    _worker_bars: dict[str, int] = field(default_factory=dict)

    def worker_bar(self, status: str) -> int:
        """Theme tag for a worker progress bar in the given status."""
        return self._worker_bars.get(status, self.bar_waiting)

    def all_tags(self) -> list[int]:
        """Every theme item created for this palette, so it can be freed."""
        tags = []
        for f in fields(self):
            if f.name == "palette":
                continue
            value = getattr(self, f.name)
            if isinstance(value, int) and value:
                tags.append(value)
        return tags


def destroy_themes(themes: Themes) -> None:
    """Delete a palette's theme items - called before switching accents."""
    for tag in themes.all_tags():
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag)


def _build_global_theme(p: Palette) -> int:
    """Application-wide colours and spacing - the base every widget inherits."""
    with dpg.theme() as theme, dpg.theme_component(dpg.mvAll):
        _color("WindowBg", p.bg_deep)
        _color("ChildBg", p.bg_panel)
        _color("PopupBg", p.bg_panel)
        _color("MenuBarBg", p.bg_elev)
        _color("Border", p.border)
        _color("BorderShadow", (0, 0, 0, 0))

        _color("Text", p.text)
        _color("TextDisabled", p.text_faint)
        _color("TextSelectedBg", p.accent_ghost)

        _color("FrameBg", p.bg_elev)
        _color("FrameBgHovered", _mix(p.bg_elev, p.accent, 0.16))
        _color("FrameBgActive", _mix(p.bg_elev, p.accent, 0.28))

        _color("TitleBg", p.bg_deep)
        _color("TitleBgActive", p.bg_elev)
        _color("TitleBgCollapsed", p.bg_deep)

        _color("Button", _mix(p.bg_elev, p.accent, 0.10))
        _color("ButtonHovered", _mix(p.bg_elev, p.accent, 0.30))
        _color("ButtonActive", _mix(p.bg_elev, p.accent, 0.48))

        _color("Header", _mix(p.bg_elev, p.accent, 0.20))
        _color("HeaderHovered", _mix(p.bg_elev, p.accent, 0.32))
        _color("HeaderActive", _mix(p.bg_elev, p.accent, 0.44))

        _color("Separator", p.border)
        _color("SeparatorHovered", p.accent_dim)
        _color("SeparatorActive", p.accent)

        _color("CheckMark", p.accent)
        _color("SliderGrab", p.accent_dim)
        _color("SliderGrabActive", p.accent)
        _color("ResizeGrip", (0, 0, 0, 0))
        _color("ResizeGripHovered", p.accent_ghost)
        _color("ResizeGripActive", p.accent_dim)

        _color("ScrollbarBg", p.bg_deep)
        _color("ScrollbarGrab", p.accent_soft)
        _color("ScrollbarGrabHovered", p.accent_dim)
        _color("ScrollbarGrabActive", p.accent)

        _color("PlotHistogram", p.accent)
        _color("PlotHistogramHovered", p.accent_bright)
        _color("PlotLines", p.accent_dim)

        _color("TableHeaderBg", p.bg_elev)
        _color("TableBorderStrong", p.border)
        _color("TableBorderLight", _mix(p.bg_panel, p.border, 0.55))
        _color("TableRowBg", (0, 0, 0, 0))
        _color("TableRowBgAlt", (255, 255, 255, 6))

        _color("Tab", p.bg_elev)
        _color("TabHovered", _mix(p.bg_elev, p.accent, 0.30))
        _color("TabActive", _mix(p.bg_elev, p.accent, 0.22))
        _color("TabSelected", _mix(p.bg_elev, p.accent, 0.22))

        # Sharp corners everywhere - rounded widgets read as "app", not "HUD".
        _style("WindowRounding", 0)
        _style("ChildRounding", 0)
        _style("FrameRounding", 0)
        _style("PopupRounding", 0)
        _style("ScrollbarRounding", 0)
        _style("GrabRounding", 0)
        _style("TabRounding", 0)

        _style("WindowBorderSize", 1)
        _style("ChildBorderSize", 1)
        _style("FrameBorderSize", 1)
        _style("PopupBorderSize", 1)

        _style("WindowPadding", 8, 8)
        _style("FramePadding", 8, 4)
        _style("CellPadding", 6, 3)
        _style("ItemSpacing", 7, 5)
        _style("ItemInnerSpacing", 6, 4)
        _style("ScrollbarSize", 11)
        _style("GrabMinSize", 9)
    return theme


def build_themes(palette: Palette) -> Themes:
    """Create every Dear PyGui theme object for `palette` and return their tags."""
    t = Themes(palette=palette)
    p = palette

    t.global_theme = _build_global_theme(p)

    # ----- containers --------------------------------------------------------
    with dpg.theme() as t.panel, dpg.theme_component(dpg.mvAll):
        _color("ChildBg", p.bg_panel)
        _color("Border", p.border)
        _style("ChildBorderSize", 1)
        _style("WindowPadding", 9, 7)

    with dpg.theme() as t.plain_panel, dpg.theme_component(dpg.mvAll):
        _color("ChildBg", (0, 0, 0, 0))
        _color("Border", (0, 0, 0, 0))
        _style("ChildBorderSize", 0)
        _style("WindowPadding", 0, 0)

    with dpg.theme() as t.log_surface, dpg.theme_component(dpg.mvAll):
        _color("ChildBg", p.bg_input)
        _color("Border", p.border)
        _style("ChildBorderSize", 1)
        _style("WindowPadding", 6, 4)
        _style("ItemSpacing", 4, 1)      # tight, terminal-like line spacing

    with dpg.theme() as t.tile, dpg.theme_component(dpg.mvAll):
        _color("ChildBg", p.bg_elev)
        _color("Border", _mix(p.bg_elev, p.accent, 0.35))
        _style("ChildBorderSize", 1)
        _style("WindowPadding", 8, 5)
        _style("ItemSpacing", 4, 1)

    with dpg.theme() as t.table, dpg.theme_component(dpg.mvAll):
        _color("Header", p.accent_ghost)
        _style("CellPadding", 5, 2)

    # ----- buttons -----------------------------------------------------------
    t.btn_start = _button_theme(p.accent, p.bg_deep)
    t.btn_pause = _button_theme(p.warning, p.bg_deep)
    t.btn_stop = _button_theme(p.error, p.bg_deep)
    t.btn_ghost = _button_theme(p.text_dim, p.text, filled=False, palette=p)

    # ----- progress bars -----------------------------------------------------
    t.bar_waiting = _bar_theme(p.accent_soft, p)
    t.bar_scanning = _bar_theme(p.accent, p)
    t.bar_verifying = _bar_theme(p.info, p)
    t.bar_found = _bar_theme(p.found, p)
    t.bar_stopped = _bar_theme(p.text_faint, p)

    t.bar_scan_phase = _bar_theme(p.accent, p, height_pad=3)
    t.bar_verify_phase = _bar_theme(p.info, p, height_pad=3)
    t.bar_merge_phase = _bar_theme(p.found, p, height_pad=3)

    t._worker_bars = {
        "WAITING": t.bar_waiting,
        "SCANNING": t.bar_scanning,
        "VERIFYING": t.bar_verifying,
        "FOUND": t.bar_found,
        "STOPPED": t.bar_stopped,
    }

    return t


def _button_theme(tint: RGBA, label_color: RGBA, *, filled: bool = True,
                  palette: Optional[Palette] = None) -> int:
    """A flat button whose fill is a dark wash of `tint`, brightening on hover."""
    with dpg.theme() as theme, dpg.theme_component(dpg.mvAll):
        if filled:
            _color("Button", _with_alpha(tint, 210))
            _color("ButtonHovered", _with_alpha(tint, 255))
            _color("ButtonActive", _shade(tint, 0.75))
            _color("Text", label_color)
            _color("Border", _with_alpha(tint, 255))
        else:
            base = palette.bg_elev if palette else (18, 24, 32, 255)
            _color("Button", (0, 0, 0, 0))
            _color("ButtonHovered", _mix(base, tint, 0.30))
            _color("ButtonActive", _mix(base, tint, 0.50))
            _color("Text", label_color)
            _color("Border", _with_alpha(tint, 140))
        _style("FrameRounding", 0)
        _style("FrameBorderSize", 1)
        _style("FramePadding", 10, 5)
    return theme


def _bar_theme(fill: RGBA, palette: Palette, height_pad: int = 0) -> int:
    """Progress bar with a neon fill over a dark trough."""
    with dpg.theme() as theme, dpg.theme_component(dpg.mvAll):
        _color("PlotHistogram", fill)
        _color("FrameBg", palette.bg_input)
        _color("Border", _mix(palette.bg_panel, fill, 0.35))
        _color("Text", palette.text)
        _style("FrameRounding", 0)
        _style("FrameBorderSize", 1)
        if height_pad:
            _style("FramePadding", 8, height_pad)
    return theme


# ═══════════════════════════════════════════════════════════════════════════════
#  Colour maths
# ═══════════════════════════════════════════════════════════════════════════════
def _mix(a: Sequence[int], b: Sequence[int], t: float) -> RGBA:
    """Linear blend of two colours; `t=0` is `a`, `t=1` is `b`.  Alpha from `a`."""
    return (
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
        a[3] if len(a) > 3 else 255,
    )


def _shade(color: Sequence[int], factor: float) -> RGBA:
    """Darken (factor < 1) or lighten (factor > 1) while keeping alpha."""
    return (
        max(0, min(255, int(color[0] * factor))),
        max(0, min(255, int(color[1] * factor))),
        max(0, min(255, int(color[2] * factor))),
        color[3] if len(color) > 3 else 255,
    )


def _with_alpha(color: Sequence[int], alpha: int) -> RGBA:
    return (color[0], color[1], color[2], alpha)


# Public aliases - panels use these for drawlist colours, which are not themed.
mix = _mix
shade = _shade
with_alpha = _with_alpha


def log_color(level: str, palette: Palette) -> RGBA:
    """Colour for a STREAM LOG line of the given severity."""
    return {
        "DEBUG": palette.text_faint,
        "INFO": palette.text,
        "SUCCESS": palette.success,
        "WARN": palette.warning,
        "WARNING": palette.warning,
        "ERROR": palette.error,
    }.get(level, palette.text)


def status_color(status: str, palette: Palette) -> RGBA:
    """Colour for a worker status label."""
    return {
        "WAITING": palette.text_faint,
        "SCANNING": palette.accent,
        "VERIFYING": palette.info,
        "FOUND": palette.found,
        "STOPPED": palette.text_dim,
    }.get(status, palette.text_dim)
