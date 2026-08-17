"""Rich live dashboard - renders scanner statistics in real-time."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Optional

from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mining_dark.checkers.balance_checker import ScanStats
from mining_dark.core.wallet import FoundWallet, WalletKeys
from mining_dark.i18n import t
from mining_dark.utils.utxo_db import UTXODatabase


_BANNER = "[bold cyan]Mining-Dark  ·  Bitcoin Balance Scanner Pro[/bold cyan]"


class Dashboard:
    """Manages a Rich Live context that refreshes the terminal dashboard at a configurable FPS."""

    def __init__(
        self,
        stats: ScanStats,
        utxo_db: Optional[UTXODatabase] = None,
        recent_rows: int = 15,
        refresh_fps: int = 4,
    ) -> None:
        self._stats = stats
        self._utxo_db = utxo_db
        self._max_recent = recent_rows
        self._fps = refresh_fps

        self._recent: "deque[WalletKeys]" = deque(maxlen=recent_rows)
        self._found: list[FoundWallet] = []
        self._console = Console()
        self._live: Optional[Live] = None

    def record_key(self, wallet: WalletKeys) -> None:
        self._recent.appendleft(wallet)

    def record_found(self, found: FoundWallet) -> None:
        self._found.append(found)

    async def run(self) -> None:
        interval = 1.0 / max(1, self._fps)
        with Live(
            self._render(),
            console=self._console,
            refresh_per_second=self._fps,
            screen=False,
            transient=False,
        ) as live:
            self._live = live
            try:
                while True:
                    await asyncio.sleep(interval)
                    live.update(self._render())
            except asyncio.CancelledError:
                return

    def _render(self) -> Group:
        return Group(
            self._render_header(),
            Columns(
                [self._render_stats(), self._render_utxo_status()],
                equal=True,
                expand=True,
            ),
            self._render_recent_table(),
            self._render_found_table(),
        )

    def _render_header(self) -> Panel:
        return Panel(
            Align.center(Text.from_markup(_BANNER)),
            box=box.DOUBLE_EDGE,
            style="bold",
        )

    def _render_stats(self) -> Panel:
        s = self._stats
        elapsed = s.elapsed_seconds
        hh = int(elapsed // 3600)
        mm = int((elapsed % 3600) // 60)
        ss = int(elapsed % 60)

        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim", no_wrap=True)
        table.add_column(style="bold yellow", justify="right")

        rows = [
            (t("dash.stats.elapsed"),   f"{hh:02d}:{mm:02d}:{ss:02d}"),
            (t("dash.stats.keys"),      f"{s.keys_generated:,}"),
            (t("dash.stats.addresses"), f"{s.addresses_checked:,}"),
            (t("dash.stats.kps"),       f"{s.keys_per_second:.1f}"),
            (t("dash.stats.cps"),       f"{s.checks_per_second:.1f}"),
            (t("dash.stats.found"),
             f"[bold green]{s.wallets_found}[/bold green]" if s.wallets_found else "0"),
            (t("dash.stats.btc"),
             f"[bold green]{s.total_found_satoshis / 1e8:.8f}[/bold green]"
             if s.total_found_satoshis else "0.00000000"),
        ]
        for label, value in rows:
            table.add_row(label, Text.from_markup(value))

        return Panel(table, title=f"[bold]{t('dash.stats.title')}[/bold]", box=box.ROUNDED)

    def _render_utxo_status(self) -> Panel:
        db = self._utxo_db

        if db is None or not db.exists:
            dot   = "[red]*[/red]"
            label = f"[red]{t('dash.utxo.missing')}[/red]"
            status_style = "red"
        elif db.needs_update:
            dot   = "[red]*[/red]"
            label = f"[red]{t('dash.utxo.outdated')}[/red] [dim]({db.age_days}d)[/dim]"
            status_style = "red"
        else:
            dot   = "[green]*[/green]"
            label = f"[green]{t('dash.utxo.current')}[/green]"
            status_style = "green"

        table = Table.grid(padding=(0, 2))
        table.add_column(style="dim", no_wrap=True)
        table.add_column(no_wrap=True)

        table.add_row(t("dash.utxo.status"), Text.from_markup(f"{dot}  {label}"))

        if db and db.exists:
            lu = db.last_updated
            table.add_row(
                t("dash.utxo.updated"),
                lu.strftime("%d/%m/%Y %H:%M") if lu else "-",
            )
            table.add_row(
                t("dash.utxo.addresses"),
                f"{db.address_count:,}" if db.address_count else "-",
            )
            table.add_row(
                t("dash.utxo.size"),
                f"{db.db_size_mb:.1f} MB" if db.db_size_mb > 0 else "-",
            )
            table.add_row(t("dash.utxo.source"), db.source)
        else:
            table.add_row("", Text.from_markup(t("dash.utxo.run_cmd")))

        return Panel(
            table,
            title=f"[bold {status_style}]{t('dash.utxo.title')}[/bold {status_style}]",
            box=box.ROUNDED,
        )

    def _render_recent_table(self) -> Panel:
        def abbr(addr: str) -> str:
            return addr[:10] + "..." if len(addr) > 10 else addr

        table = Table(
            box=box.SIMPLE_HEAD,
            expand=True,
            show_lines=False,
            header_style="bold cyan",
        )
        table.add_column("P2PKH",     style="dim", no_wrap=True)
        table.add_column("P2PKH-U",   style="dim", no_wrap=True)
        table.add_column("P2SH",      style="dim", no_wrap=True)
        table.add_column("P2WPKH",    style="dim", no_wrap=True)
        table.add_column("P2WSH",     style="dim", no_wrap=True)
        table.add_column("P2TR",      style="dim", no_wrap=True)

        for wallet in list(self._recent):
            table.add_row(
                abbr(wallet.p2pkh),
                abbr(wallet.p2pkh_uncompressed),
                abbr(wallet.p2sh_p2wpkh),
                abbr(wallet.p2wpkh),
                abbr(wallet.p2wsh),
                abbr(wallet.p2tr),
            )

        return Panel(table, title="[bold]Addresses Checked[/bold]", box=box.ROUNDED)

    def _render_found_table(self) -> Panel:
        if not self._found:
            content = Align.center(
                Text("No wallets with balance found yet.", style="dim italic")
            )
            return Panel(content, title="[bold green]Found Wallets[/bold green]", box=box.ROUNDED)

        table = Table(box=box.SIMPLE_HEAD, expand=True, header_style="bold green")
        table.add_column("Discovered At", no_wrap=True, max_width=20)
        table.add_column("Address", no_wrap=True)
        table.add_column("Type", max_width=12)
        table.add_column("Confirmed BTC", justify="right", max_width=18)
        table.add_column("Unconfirmed BTC", justify="right", max_width=18)

        for fw in self._found[-10:]:
            table.add_row(
                fw.discovered_at.strftime("%Y-%m-%d %H:%M:%S"),
                fw.primary_address,
                fw.primary_address_type,
                f"[bold green]{fw.total_confirmed_satoshis / 1e8:.8f}[/bold green]",
                f"{fw.total_unconfirmed_satoshis / 1e8:.8f}",
            )

        return Panel(table, title="[bold green]Found Wallets[/bold green]", box=box.ROUNDED)
