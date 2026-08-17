"""Interactive setup menu displayed before scan starts."""

from __future__ import annotations

from pathlib import Path

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from mining_dark.config.settings import Settings
from mining_dark.i18n import t
from mining_dark.utils.utxo_db import UPDATE_INTERVAL_DAYS, UTXODatabase

console = Console()

_BANNER = """
[bold cyan]███╗   ███╗██╗███╗   ██╗██╗███╗   ██╗ ██████╗ [/bold cyan]
[bold cyan]████╗ ████║██║████╗  ██║██║████╗  ██║██╔════╝ [/bold cyan]
[bold cyan]██╔████╔██║██║██╔██╗ ██║██║██╔██╗ ██║██║  ███╗[/bold cyan]
[bold cyan]██║╚██╔╝██║██║██║╚██╗██║██║██║╚██╗██║██║   ██║[/bold cyan]
[bold cyan]██║ ╚═╝ ██║██║██║ ╚████║██║██║ ╚████║╚██████╔╝[/bold cyan]
[bold cyan]╚═╝     ╚═╝╚═╝╚═╝  ╚═══╝╚═╝╚═╝  ╚═══╝ ╚═════╝ [/bold cyan]
[bold cyan]██████╗  █████╗ ██████╗ ██╗  ██╗[/bold cyan]
[bold cyan]██╔══██╗██╔══██╗██╔══██╗██║ ██╔╝[/bold cyan]
[bold cyan]██║  ██║███████║██████╔╝█████╔╝ [/bold cyan]
[bold cyan]██║  ██║██╔══██║██╔══██╗██╔═██╗ [/bold cyan]
[bold cyan]██████╔╝██║  ██║██║  ██║██║  ██╗[/bold cyan]
[bold cyan]╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝[/bold cyan]
[bold orange1]₿itcoin[/bold orange1]         [bold white]Balance Scanner Pro[/bold white]  [dim]by: Czar[/dim]
"""

_ALL_ADDRESS_TYPES = ["p2pkh", "p2pkh_uncompressed", "p2sh_p2wpkh", "p2wpkh", "p2wsh", "p2tr"]



def _print_banner() -> None:
    console.print(_BANNER, justify="center")
    console.rule(style="cyan dim")
    console.print()


def _show_utxo_status(db_file: Path | None = None) -> bool:
    """
    Display UTXO database status. Returns True if the database is ready.

    Takes the path explicitly because the scan opens
    `settings.utxo.resolved_db_file()`: checking the default instead meant the
    menu could report READY about a database the scan would never touch.
    """
    with UTXODatabase(db_file) as db:
        status   = db.status
        is_ready = db.is_ready

        if status == "missing":
            dot        = "[red]*[/red]"
            status_txt = f"[bold red]{t('menu.utxo.missing')}[/bold red]"
            border     = "red"
        elif status == "outdated":
            dot        = "[yellow]*[/yellow]"
            status_txt = f"[bold yellow]{t('menu.utxo.outdated')}[/bold yellow]"
            border     = "yellow"
        else:
            dot        = "[bold green]*[/bold green]"
            status_txt = f"[bold green]{t('menu.utxo.ready')}[/bold green]"
            border     = "green"

        grid = Table.grid(padding=(0, 3))
        grid.add_column(style="dim", no_wrap=True)
        grid.add_column(no_wrap=True)

        grid.add_row(t("menu.utxo.status"), Text.from_markup(f"{dot}  {status_txt}"))

        if is_ready:
            lu  = db.last_updated
            age = db.age_days
            age_txt = (
                f"[green]{age}{t('menu.utxo.days_ago')}[/green]" if age < 7
                else f"[yellow]{age}{t('menu.utxo.days_ago')}[/yellow]" if age < 30
                else f"[red]{age}{t('menu.utxo.days_ago')}[/red]"
            )
            grid.add_row(t("menu.utxo.addresses"), f"[bold cyan]{db.address_count:,}[/bold cyan]")
            grid.add_row(t("menu.utxo.updated"), Text.from_markup(
                lu.strftime("%d/%m/%Y %H:%M") + f"  [dim]({age_txt})[/dim]" if lu else "-"
            ))
            grid.add_row(t("menu.utxo.size"),   f"{db.db_size_mb:,.1f} MB")
            grid.add_row(t("menu.utxo.source"), db.source if db.source != "-" else "[dim]-[/dim]")
            if db.block_height:
                grid.add_row(t("menu.utxo.block"), f"{db.block_height:,}")
        else:
            grid.add_row("", Text.from_markup(t("menu.utxo.run_cmd")))

        console.print(Panel(
            grid,
            title=f"[bold {border}]{t('menu.utxo.title')}[/bold {border}]",
            border_style=border,
            box=box.ROUNDED,
            padding=(1, 2),
        ))
        console.print()

        if status == "missing":
            console.print(t("menu.utxo.missing_msg"))
            return False

        if status == "outdated":
            console.print(t("menu.utxo.outdated_msg", days=UPDATE_INTERVAL_DAYS))

        return is_ready


def _choose_mode() -> str:
    console.print(t("menu.mode.random"))
    console.print(t("menu.mode.hd"))
    console.print()
    choice = Prompt.ask(t("menu.mode.prompt"), choices=["1", "2"], default="1")
    return "random" if choice == "1" else "hd"


def _ask_int_in_range(prompt: str, default: int, low: int, high: int) -> int:
    """
    Ask until the answer is one the config models accept.

    IntPrompt has no bounds of its own, so a typed 0 used to travel all the way
    into the scanner - `range(0)` starts no workers at all, and the scan then
    generates keys while checking nothing.
    """
    while True:
        value = IntPrompt.ask(prompt, default=default)
        if low <= value <= high:
            return value
        console.print(t("menu.out_of_range", low=low, high=high))


def _choose_workers() -> int:
    console.print()
    console.print(t("menu.workers.hint"))
    console.print()
    return _ask_int_in_range(t("menu.workers.prompt"), default=10, low=1, high=512)


def _choose_child_count() -> int:
    console.print()
    console.print(t("menu.child.hint"))
    console.print()
    return _ask_int_in_range(t("menu.child.prompt"), default=20, low=1, high=10_000)


# Label, description key and example per address type, in display order.
_ADDRESS_ROWS: list[tuple[str, str, str, str]] = [
    ("p2pkh",              "P2PKH",        "menu.addr.p2pkh",   "1..."),
    ("p2pkh_uncompressed", "P2PKH uncomp", "menu.addr.p2pkh_u", "1..."),
    ("p2sh_p2wpkh",        "P2SH-P2WPKH",  "menu.addr.p2sh",    "3..."),
    ("p2wpkh",             "P2WPKH",       "menu.addr.p2wpkh",  "bc1q... (42)"),
    ("p2wsh",              "P2WSH",        "menu.addr.p2wsh",   "bc1q... (62)"),
    ("p2tr",               "P2TR",         "menu.addr.p2tr",    "bc1p..."),
]


def _show_summary(
    mode: str,
    workers: int,
    child_count: int | None = None,
    address_types: list[str] | None = None,
) -> None:
    console.print()

    # Whatever config.yaml enables, not a fixed list - the menu used to display
    # all six and then overwrite the user's selection to match.
    address_types = address_types if address_types is not None else _ALL_ADDRESS_TYPES

    addr_table = Table(box=None, show_header=False, padding=(0, 2))
    addr_table.add_column(style="cyan", no_wrap=True)
    addr_table.add_column(no_wrap=True)
    addr_table.add_column(style="dim", no_wrap=True)
    for key, label, description, example in _ADDRESS_ROWS:
        if key not in address_types:
            continue
        note = f"  {t('menu.addr.p2pkh_u_note')}" if key == "p2pkh_uncompressed" else ""
        addr_table.add_row(label, t(description), f"{example}{note}")

    config_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    config_table.add_column(style="dim", no_wrap=True)
    config_table.add_column(style="bold yellow")
    config_table.add_row(t("menu.summary.mode"),    mode.upper())
    config_table.add_row(t("menu.summary.workers"), str(workers))
    if child_count is not None:
        keys_per_seed = child_count * 4
        config_table.add_row(
            t("menu.summary.child"),
            f"{child_count}  [dim]({keys_per_seed} {t('menu.summary.keys_per_seed')})[/dim]",
        )

    console.print(Columns(
        [
            Panel(
                config_table,
                title=f"[bold]{t('menu.summary.config')}[/bold]",
                box=box.ROUNDED,
                padding=(1, 2),
            ),
            Panel(
                addr_table,
                title=f"[bold]{t('menu.summary.addresses')}[/bold]",
                box=box.ROUNDED,
                padding=(1, 2),
            ),
        ],
        expand=True,
    ))
    console.print()


def run_setup(settings: Settings) -> Settings:
    """Display interactive setup menu and return updated Settings."""
    console.clear()
    _print_banner()

    console.rule(f"[bold cyan]{t('menu.rule.utxo')}[/bold cyan]", style="cyan dim")
    console.print()

    if not _show_utxo_status(settings.utxo.resolved_db_file()):
        raise SystemExit(1)

    if not Confirm.ask(t("menu.confirm.continue"), default=True):
        console.print(t("menu.cancelled"))
        raise SystemExit(0)

    console.print()
    console.rule(f"[bold cyan]{t('menu.rule.config')}[/bold cyan]", style="cyan dim")
    console.print()

    mode    = _choose_mode()
    workers = _choose_workers()

    child_count = None
    if mode == "hd":
        console.print()
        console.rule(f"[bold cyan]{t('menu.rule.hd')}[/bold cyan]", style="cyan dim")
        child_count = _choose_child_count()

    _show_summary(mode, workers, child_count, settings.scanner.address_types)

    if not Confirm.ask(t("menu.confirm.start"), default=True):
        console.print(t("menu.cancelled"))
        raise SystemExit(0)

    # address_types is deliberately left alone: the menu never asks about it,
    # so overwriting it here silently discarded whatever config.yaml selected.
    settings.scanner.mode    = mode
    settings.scanner.workers = workers
    if child_count is not None:
        settings.hd_wallet.child_count = child_count

    console.print()
    console.rule(style="cyan dim")
    console.print()

    return settings
