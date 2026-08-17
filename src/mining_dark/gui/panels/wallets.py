"""
Found-wallets browser.

The files this lists contain the private keys of anything the scanner found,
so the dialog is built around not putting them on screen by accident:

  * the table shows only public metadata, read from the `.json` sidecar;
  * "open" hands the file to the desktop, so the keys never enter this
    process's framebuffer and cannot land in a screenshot of the dashboard;
  * the in-app preview masks every key by default, behind an explicit toggle
    that carries a warning.
"""

from __future__ import annotations

import json
from pathlib import Path

import dearpygui.dearpygui as dpg

from mining_dark.i18n import t
from mining_dark.gui.panels.common import (
    PanelContext,
    fit_and_center,
    panel_title,
    set_text,
    styled_text,
)
from mining_dark.gui.services import open_in_desktop
from mining_dark.gui.state import abbreviate
from mining_dark.utils.logger import redact

_MODAL = "wallets_modal"
_TABLE = "wallets_table"
_SUBTITLE = "wallets_subtitle"
_PREVIEW = "wallets_preview"
_STATUS = "wallets_status"
_REVEAL = "wallets_reveal"

_MAX_ROWS = 400

_MODAL_W = 1120
_MODAL_H = 700


class WalletRow:
    """One discovered wallet, as far as this dialog is allowed to know."""

    __slots__ = ("address", "address_type", "btc", "discovered_at", "json_path", "txt_path")

    def __init__(self, txt_path: Path, json_path: Path) -> None:
        self.txt_path = txt_path
        self.json_path = json_path
        self.discovered_at = "?"
        self.address = "?"
        self.address_type = "?"
        self.btc = 0.0

        if json_path.exists():
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                self.discovered_at = str(data.get("discovered_at", "?"))
                balances = data.get("balances") or []
                if balances:
                    self.address = str(balances[0].get("address", "?"))
                    self.address_type = str(balances[0].get("address_type", "?"))
                else:
                    self.address = next(iter((data.get("addresses") or {}).values()), "?")
                self.btc = float(data.get("total_confirmed_satoshis", 0)) / 1e8
            except (OSError, ValueError, AttributeError, TypeError):
                # A malformed or foreign-schema .json - an old version, a hand
                # edit, a partial file from another tool - must not take the
                # whole listing down.  The row keeps its "?" placeholders and the
                # file stays listed and openable, exactly as the CLI `found`
                # command already tolerates.  Only json.loads used to be guarded,
                # so a valid JSON of an unexpected shape (a string where a number
                # belonged) crashed here and broke the browser for every wallet.
                return


class WalletsDialog:
    """Modal listing the wallets already written to `data/found_wallets/`."""

    def __init__(self, ctx: PanelContext, settings, on_log=None) -> None:
        self.ctx = ctx
        self.settings = settings
        self._on_log = on_log
        self._rows: list[WalletRow] = []
        self._selected: int = -1
        self._reveal = False
        self._settle = 0

    # ----- construction ------------------------------------------------------
    @property
    def directory(self) -> Path:
        return self.settings.output.resolved_found_wallets_dir()

    def build(self) -> None:
        ctx = self.ctx
        p = ctx.palette

        with dpg.window(tag=_MODAL, label=t("wallets.title"), modal=True, show=False,
                        width=_MODAL_W, height=_MODAL_H, no_collapse=True,
                        no_scrollbar=True, on_close=self.close) as modal:
            dpg.bind_item_theme(modal, ctx.themes.panel)

            panel_title(ctx, t("wallets.title"), "", subtitle_tag=_SUBTITLE)

            with dpg.group(horizontal=True):
                self._button(t("wallets.refresh"), self.refresh, ctx.themes.btn_ghost, 118)
                self._button(t("wallets.open_folder"), self._open_folder,
                             ctx.themes.btn_ghost, 148)
                self._button(t("wallets.open_file"), self._open_file,
                             ctx.themes.btn_start, 138)
                self._button(t("settings.close"), self.close, ctx.themes.btn_stop, 118)

            dpg.add_spacer(height=4)

            with dpg.child_window(height=ctx.px(250), border=True), \
                    dpg.table(tag=_TABLE, header_row=True,
                              policy=dpg.mvTable_SizingStretchProp,
                              borders_innerH=False, borders_innerV=False,
                              row_background=True, scrollY=True,
                              freeze_rows=1) as table:
                dpg.add_table_column(label=t("wallets.col_date"), width_fixed=True,
                                     init_width_or_weight=180)
                dpg.add_table_column(label=t("wallets.col_address"),
                                     init_width_or_weight=1.0)
                dpg.add_table_column(label=t("wallets.col_type"), width_fixed=True,
                                     init_width_or_weight=150)
                dpg.add_table_column(label=t("wallets.col_btc"), width_fixed=True,
                                     init_width_or_weight=130)
                dpg.add_table_column(label=t("wallets.col_file"), width_fixed=True,
                                     init_width_or_weight=260)
                dpg.bind_item_theme(table, ctx.themes.table)

            dpg.add_spacer(height=4)

            with dpg.group(horizontal=True):
                styled_text(t("wallets.preview"), color=p.accent, font=ctx.font("h2"))
                dpg.add_checkbox(label=t("wallets.reveal"), tag=_REVEAL,
                                 default_value=False, callback=self._on_reveal)
                styled_text("", color=p.warning, font=ctx.font("small"), tag=_STATUS)

            dpg.add_input_text(tag=_PREVIEW, multiline=True, readonly=True,
                               width=-1, height=-1, default_value="")

    def _button(self, label: str, callback, theme: int, width: int) -> None:
        item = dpg.add_button(label=label, width=width, callback=lambda *_: callback())
        dpg.bind_item_theme(item, theme)

    # ----- open / close ------------------------------------------------------
    def open(self) -> None:
        if not dpg.does_item_exist(_MODAL):
            return
        self.refresh()
        dpg.configure_item(_MODAL, show=True)
        self._settle = 3
        fit_and_center(_MODAL, max_width=_MODAL_W, max_height=_MODAL_H)

    def close(self) -> None:
        if dpg.does_item_exist(_MODAL):
            dpg.configure_item(_MODAL, show=False)

    @property
    def is_open(self) -> bool:
        return dpg.does_item_exist(_MODAL) and dpg.is_item_shown(_MODAL)

    def update(self) -> None:
        """Let the modal settle at the right size after a rebuild.  See settings."""
        if self._settle and self.is_open:
            self._settle -= 1
            fit_and_center(_MODAL, max_width=_MODAL_W, max_height=_MODAL_H)

    # ----- data --------------------------------------------------------------
    def refresh(self) -> None:
        """Re-scan the output directory and rebuild the table."""
        self._rows = self._scan()
        self._selected = -1
        self._render_rows()
        self._render_preview()

        set_text(_SUBTITLE, t("wallets.count", count=len(self._rows),
                              path=str(self.directory)))

    def _scan(self) -> list[WalletRow]:
        from mining_dark.utils.file_manager import find_wallet_files

        # Shared with `mining-dark found` so the two listings cannot drift.
        return [
            WalletRow(txt, txt.with_suffix(".json"))
            for txt in find_wallet_files(self.directory)[:_MAX_ROWS]
        ]

    def _render_rows(self) -> None:
        if not dpg.does_item_exist(_TABLE):
            return

        for child in dpg.get_item_children(_TABLE, slot=1) or []:
            dpg.delete_item(child)

        ctx = self.ctx
        p = ctx.palette

        if not self._rows:
            with dpg.table_row(parent=_TABLE):
                styled_text(t("wallets.empty"), color=p.text_dim, font=ctx.font("small"))
            return

        for index, row in enumerate(self._rows):
            with dpg.table_row(parent=_TABLE):
                dpg.add_selectable(label=row.discovered_at, span_columns=True,
                                   tag=f"wl_sel_{index}",
                                   callback=self._make_select(index))
                styled_text(abbreviate(row.address, 20, 10), color=p.text,
                            font=ctx.font("tiny"))
                styled_text(row.address_type, color=p.text_dim, font=ctx.font("tiny"))
                styled_text(f"{row.btc:.8f}", color=p.accent_bright, font=ctx.font("tiny"))
                styled_text(row.txt_path.name, color=p.text_faint, font=ctx.font("tiny"))

    def _make_select(self, index: int):
        def _callback(*_: object) -> None:
            for other in range(len(self._rows)):
                tag = f"wl_sel_{other}"
                if dpg.does_item_exist(tag):
                    dpg.set_value(tag, other == index)
            self._selected = index
            self._render_preview()
        return _callback

    # ----- preview -----------------------------------------------------------
    def _on_reveal(self, _sender, value: bool) -> None:
        self._reveal = bool(value)
        self._render_preview()

    def _render_preview(self) -> None:
        if not dpg.does_item_exist(_PREVIEW):
            return

        if self._selected < 0 or self._selected >= len(self._rows):
            dpg.set_value(_PREVIEW, t("wallets.select_row"))
            set_text(_STATUS, "", self.ctx.palette.text_dim)
            return

        path = self._rows[self._selected].txt_path
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            dpg.set_value(_PREVIEW, t("wallets.open_failed", error=str(exc)))
            return

        if self._reveal:
            dpg.set_value(_PREVIEW, content)
            set_text(_STATUS, t("wallets.reveal_warning"), self.ctx.palette.error)
        else:
            dpg.set_value(_PREVIEW, redact(content))
            set_text(_STATUS, t("wallets.masked_note"), self.ctx.palette.text_dim)

    # ----- actions -----------------------------------------------------------
    def _open_file(self) -> None:
        if self._selected < 0 or self._selected >= len(self._rows):
            self._log(t("wallets.select_row"))
            return
        self._open(self._rows[self._selected].txt_path)

    def _open_folder(self) -> None:
        self._open(self.directory)

    def _open(self, target: Path) -> None:
        try:
            open_in_desktop(target)
        except (OSError, FileNotFoundError) as exc:
            self._log(t("wallets.open_failed", error=str(exc)))
        else:
            self._log(t("wallets.opened", path=str(target)))

    def _log(self, message: str) -> None:
        if self._on_log is not None:
            self._on_log(message)
