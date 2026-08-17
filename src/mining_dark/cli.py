"""
Mining-Dark - command-line interface.

Subcommands:
  mining-dark scan             Start the balance scanner
  mining-dark check <address>  Check a single address in the UTXO database
  mining-dark found            List previously found wallets
  mining-dark keygen           Generate sample wallets (no balance check)
  mining-dark paths            Print resolved data paths
  mining-dark utxo update      Rebuild the UTXO database from Bitcoin Core
  mining-dark utxo status      Show UTXO database health
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from pydantic import ValidationError
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from mining_dark import paths
from mining_dark.config.settings import (
    ConfigError,
    Settings,
    describe_validation_error,
    load_settings,
)
from mining_dark.i18n import LANGUAGES, set_language, t
from mining_dark.utils.logger import setup_logger
from mining_dark.utils.utxo_db import UPDATE_INTERVAL_DAYS

app = typer.Typer(
    name="mining-dark",
    help="Mining-Dark - Bitcoin Balance Scanner Pro (educational Bitcoin cryptography tool).",
    add_completion=False,
    no_args_is_help=True,
)

utxo_app = typer.Typer(help="Manage the local UTXO database.", no_args_is_help=True)
app.add_typer(utxo_app, name="utxo")

node_app = typer.Typer(
    help="Manage the Bitcoin Core node (always uses data/bitcoin-core/).",
    no_args_is_help=True,
)
app.add_typer(node_app, name="node")

console = Console()

#: Set by `--lang`, and it outranks `ui.language` from config.yaml: a flag typed
#: for one invocation should not be overruled by the file it is overriding.
_lang_override: Optional[str] = None


@app.callback()
def _global_options(
    lang: Optional[str] = typer.Option(
        None, "--lang", "-l",
        help=f"Interface language: {' | '.join(LANGUAGES)} (this run only)",
    ),
) -> None:
    # No docstring on purpose: Typer would show it instead of the `help=` above.
    global _lang_override
    if lang:
        _lang_override = set_language(lang)


def _bootstrap(config: Optional[Path] = None) -> Settings:
    """Ensure data dirs exist, load settings, configure logging.  Shared by every command."""
    paths.ensure_data_dirs()

    try:
        settings = load_settings(config)
    except ConfigError as exc:
        console.print(Panel(
            f"[bold red]{exc}[/bold red]\n\n"
            f"[dim]{t('cli.config.hint')}[/dim]",
            box=box.ROUNDED, title=f"[red]{t('cli.config.invalid')}[/red]",
        ))
        raise typer.Exit(1) from None

    set_language(_lang_override or settings.ui.language)

    setup_logger(
        level=settings.logging.level,
        logs_dir=settings.logging.resolved_logs_dir(),
        rotation=settings.logging.rotation,
        retention=settings.logging.retention,
    )
    return settings


# ----- scan ------------------------------------------------------------------
@app.command()
def scan(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
    mode: Optional[str] = typer.Option(None, "--mode", "-m", help="random | hd"),
    workers: Optional[int] = typer.Option(None, "--workers", "-w", help="Parallel worker count"),
    no_menu: bool = typer.Option(False, "--no-menu", help="Skip interactive setup menu"),
) -> None:
    """Start the balance scanner (Ctrl+C to stop gracefully)."""
    settings = _bootstrap(config)

    # `workers is not None` rather than a truth test: --workers 0 is a value the
    # user typed and must be rejected, not treated as "flag absent".
    interactive = not (mode or workers is not None or no_menu)

    if interactive:
        from mining_dark.ui.setup_menu import run_setup
        settings = run_setup(settings)
    else:
        try:
            if mode:
                settings.scanner.mode = mode
            if workers is not None:
                settings.scanner.workers = workers
        except ValidationError as exc:
            console.print(
                f"[bold red]{t('cli.config.invalid_value')}[/bold red] "
                f"{describe_validation_error(exc)}"
            )
            raise typer.Exit(2) from None

    from mining_dark.utils import db_lock

    try:
        # Held for the whole scan so a rebuild cannot swap the database out
        # from under it - the reader would survive but go on answering from
        # the deleted file, with nothing reporting it.
        with db_lock.reading(settings.utxo.resolved_db_file()):
            asyncio.run(_run_scan(settings))
    except db_lock.DatabaseBusyError as exc:
        console.print(Panel(
            f"[bold yellow]{exc}[/bold yellow]",
            box=box.ROUNDED, title=f"[yellow]{t('cli.db.busy')}[/yellow]",
        ))
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        console.print(f"\n[yellow]{t('cli.scan.interrupted')}[/yellow]")


async def _run_scan(settings: Settings) -> None:
    from mining_dark.checkers.balance_checker import BalanceChecker, ScanStats
    from mining_dark.core.wallet import FoundWallet, WalletKeys
    from mining_dark.generators.hd_generator import HDWalletGenerator
    from mining_dark.generators.random_generator import RandomKeyGenerator
    from mining_dark.ui.dashboard import Dashboard
    from mining_dark.utils.file_manager import SHUTDOWN, FileManager, shutdown_persistence
    from mining_dark.utils.utxo_db import UTXODatabase

    key_queue: "asyncio.Queue[WalletKeys]"   = asyncio.Queue(maxsize=settings.scanner.queue_size)
    found_queue: "asyncio.Queue[FoundWallet]" = asyncio.Queue()
    stats = ScanStats()

    file_manager = FileManager(
        output_dir=settings.output.resolved_found_wallets_dir(),
        save_csv=settings.output.save_csv,
        json_indent=settings.output.json_indent,
    )

    utxo_db = UTXODatabase(settings.utxo.resolved_db_file())
    utxo_db.open()

    if not utxo_db.is_ready:
        console.print(
            f"[red]{t('cli.db.missing')}[/red]\n"
            f"{t('cli.db.missing_hint')} [cyan]mining-dark utxo update[/cyan]"
        )
        utxo_db.close()
        raise typer.Exit(1)

    if utxo_db.needs_update:
        console.print(
            f"[yellow]{t('cli.scan.db_stale', days=UPDATE_INTERVAL_DAYS)}[/yellow] "
            f"[cyan]mining-dark utxo update[/cyan]"
        )

    console.print(
        f"[green]{t('cli.scan.db_loaded')}[/green] - "
        + t("cli.scan.db_loaded_detail",
            count=f"{utxo_db.address_count:,}", days=utxo_db.age_days)
    )

    dashboard = Dashboard(
        stats=stats,
        utxo_db=utxo_db,
        recent_rows=settings.ui.recent_table_rows,
        refresh_fps=settings.ui.refresh_fps,
    )

    checker = BalanceChecker(
        settings=settings,
        key_queue=key_queue,
        found_queue=found_queue,
        stats=stats,
        utxo_db=utxo_db,
        on_wallet_found=dashboard.record_found,
    )

    def on_key_generated(wallet: WalletKeys) -> None:
        dashboard.record_key(wallet)

    if settings.scanner.mode == "hd":
        generator = HDWalletGenerator(
            queue=key_queue,
            derivation_paths=settings.hd_wallet.derivation_paths,
            child_count=settings.hd_wallet.child_count,
            stats=stats,
            on_key_generated=on_key_generated,
        )
    else:
        generator = RandomKeyGenerator(
            queue=key_queue,
            stats=stats,
            on_key_generated=on_key_generated,
        )

    async def persist_found() -> None:
        while True:
            fw = await found_queue.get()
            try:
                if fw is SHUTDOWN:
                    return
                await file_manager.save(fw)
            finally:
                found_queue.task_done()

    stop_event = asyncio.Event()

    def _handle_signal(*_: object) -> None:
        stop_event.set()
        generator.stop()
        checker.stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows fallback - not our target platform but keeps import safe
            signal.signal(sig, lambda *_: _handle_signal())

    persist_task = asyncio.create_task(persist_found(), name="persist")
    tasks = [
        asyncio.create_task(generator.run(),                     name="generator"),
        asyncio.create_task(checker.run(settings.scanner.workers), name="checker"),
        asyncio.create_task(dashboard.run(),                     name="dashboard"),
    ]

    try:
        await stop_event.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        # Only now that nothing else can queue a wallet: let the persistence
        # task write its backlog before it goes away.
        await shutdown_persistence(found_queue, persist_task, file_manager)
        utxo_db.close()

    console.print(
        f"\n[bold green]{t('cli.scan.ended')}[/bold green] "
        f"{t('cli.scan.keys')} [cyan]{stats.keys_generated:,}[/cyan] | "
        f"{t('cli.scan.addresses')} [cyan]{stats.addresses_checked:,}[/cyan] | "
        f"{t('cli.scan.found')} [bold green]{stats.wallets_found}[/bold green]"
    )


# ----- gui -------------------------------------------------------------------
@app.command()
def gui(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config.yaml"),
    simulate: bool = typer.Option(
        False, "--simulate", "-s",
        help="Run with simulated data - no UTXO database required",
    ),
    mode: Optional[str] = typer.Option(None, "--mode", "-m", help="random | hd"),
    workers: Optional[int] = typer.Option(None, "--workers", "-w", help="Parallel worker count"),
    theme: Optional[str] = typer.Option(None, "--theme", "-t", help="matrix | amber | ice"),
    lang: Optional[str] = typer.Option(
        None, "--lang", help="pt | en (interface only; same as the global --lang)"
    ),
    font_scale: Optional[float] = typer.Option(None, "--font-scale", help="Font size multiplier (HiDPI)"),
    autostart: bool = typer.Option(False, "--autostart", help="Start scanning immediately"),
    screenshot: Optional[Path] = typer.Option(None, "--screenshot", help="Write a PNG of the dashboard"),
    screenshot_frames: int = typer.Option(
        120, "--screenshot-frames", help="Frame at which the PNG is written"
    ),
) -> None:
    """Open the graphical dashboard (Dear PyGui)."""
    from mining_dark.gui import GUIUnavailableError, run_gui

    settings = _bootstrap(config)
    try:
        if mode:
            settings.scanner.mode = mode
        if workers is not None:
            settings.scanner.workers = workers
    except ValidationError as exc:
        console.print(
            f"[bold red]{t('cli.config.invalid_value')}[/bold red] "
            f"{describe_validation_error(exc)}"
        )
        raise typer.Exit(2) from None

    try:
        run_gui(
            simulate=simulate,
            settings=settings,
            config_path=config,
            palette=theme,
            language=lang or _lang_override,
            font_scale=font_scale,
            autostart=autostart,
            screenshot=screenshot,
            screenshot_frames=screenshot_frames,
        )
    except GUIUnavailableError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)


def _normalize_address(address: str) -> str:
    """
    Canonicalise a user-typed address for the exact-match UTXO lookup.

    Bech32 (bc1.../tb1...) is case-insensitive by BIP173 and is stored lowercase,
    so an upper- or mixed-case bech32 must be lowered or `get_balance`'s
    `WHERE address = ?` misses a funded wallet and reports "no balance" for it.
    Base58 (1.../3...) is case-sensitive and is left exactly as typed.
    """
    if address[:3].lower() in ("bc1", "tb1"):
        return address.lower()
    return address


def _guess_address_type(address: str) -> str:
    if address.startswith("1"):
        return "p2pkh"
    if address.startswith("3"):
        return "p2sh_p2wpkh"
    if address.startswith("bc1p"):
        return "p2tr"
    if address.startswith("bc1q") and len(address) == 42:
        return "p2wpkh"
    if address.startswith("bc1q") and len(address) == 62:
        return "p2wsh"
    return "unknown"


# ----- check -----------------------------------------------------------------
@app.command()
def check(
    address: str = typer.Argument(..., help="Bitcoin address to check"),
    config: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Check the balance of a single Bitcoin address in the local UTXO database."""
    settings = _bootstrap(config)
    from mining_dark.utils.utxo_db import UTXODatabase

    with UTXODatabase(settings.utxo.resolved_db_file()) as db:
        if not db.is_ready:
            console.print(
                f"[red]{t('cli.db.missing')}[/red]\n"
                f"{t('cli.db.missing_hint')} [cyan]mining-dark utxo update[/cyan]"
            )
            raise typer.Exit(1)

        address = _normalize_address(address)
        addr_type = _guess_address_type(address)
        satoshis = db.get_balance(address)

    confirmed = t("cli.check.confirmed")
    if satoshis > 0:
        console.print(
            f"[bold green]{t('cli.check.found')}[/bold green] "
            f"[cyan]{address}[/cyan] ({addr_type})\n"
            f"  {confirmed}: [bold green]{satoshis / 1e8:.8f} BTC[/bold green]"
            f"  ({satoshis:,} sat)"
        )
    else:
        console.print(
            f"[dim]{t('cli.check.empty')}[/dim] [cyan]{address}[/cyan] ({addr_type})\n"
            f"  {confirmed}: 0.00000000 BTC"
        )


# ----- found -----------------------------------------------------------------
@app.command()
def found(
    dir: Optional[Path] = typer.Option(None, "--dir", "-d", help="found_wallets directory"),
) -> None:
    """List all previously found wallets."""
    settings = _bootstrap()
    target = dir or settings.output.resolved_found_wallets_dir()

    from mining_dark.utils.file_manager import find_wallet_files

    wallet_files = find_wallet_files(target)

    if not wallet_files:
        console.print(f"[dim]{t('cli.found.empty', dir=target)}[/dim]")
        return

    table = Table(title=t("cli.found.title", dir=target), box=box.ROUNDED)
    table.add_column(t("cli.found.col_file"), style="dim", no_wrap=True)
    table.add_column(t("cli.found.col_date"))
    table.add_column(t("cli.found.col_address"))
    table.add_column(t("cli.found.col_btc"), justify="right", style="bold green")

    for txt in wallet_files:
        try:
            data = json.loads(txt.with_suffix(".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # The .txt holds the key either way, so a missing or unreadable
            # .json must not make the wallet disappear from this listing.
            discovered = datetime.fromtimestamp(txt.stat().st_mtime)
            table.add_row(
                txt.name,
                discovered.strftime("%Y-%m-%dT%H:%M:%S"),
                f"[yellow]{t('cli.found.see_txt')}[/yellow]",
                "?",
            )
            continue

        table.add_row(
            txt.name,
            data.get("discovered_at", "?"),
            next(iter(data.get("addresses", {}).values()), "?"),
            f"{data.get('total_confirmed_satoshis', 0) / 1e8:.8f}",
        )

    console.print(table)
    console.print(f"\n{t('cli.found.total')} [cyan]{len(wallet_files)}[/cyan]")


# ----- keygen ----------------------------------------------------------------
@app.command()
def keygen(
    count: int = typer.Option(1, "--count", "-n", help="Number of wallets to generate"),
) -> None:
    """Generate and display sample Bitcoin wallets (no balance check)."""
    from mining_dark.core.address_generator import AddressGenerator
    from mining_dark.core.key_generator import KeyGenerator

    table = Table(title=t("cli.keygen.title"), box=box.ROUNDED, show_lines=True)
    table.add_column("P2PKH", style="cyan")
    table.add_column("P2WPKH", style="green")
    table.add_column("P2TR", style="magenta")
    table.add_column("WIF", style="dim", no_wrap=False, max_width=55)

    for _ in range(count):
        pk = KeyGenerator.generate_private_key()
        wallet = AddressGenerator.from_private_key(pk)
        table.add_row(wallet.p2pkh, wallet.p2wpkh, wallet.p2tr, wallet.private_key_wif)

    console.print(table)
    if count > 1:
        console.print("\n" + t("cli.keygen.total", count=count))


# ----- paths -----------------------------------------------------------------
@app.command("paths")
def paths_cmd() -> None:
    """Print resolved data paths (project root, data dir, subfolders)."""
    console.print(paths.describe())


# ----- doctor ----------------------------------------------------------------
#: Colour per status.  The words themselves are translated, and padded to a
#: common width so the marks line up whatever the language spells them.
_DOCTOR_COLOUR = {"ok": "green", "warn": "yellow", "fail": "red"}


def _doctor_mark(status: str) -> str:
    label = t(f"doctor.mark.{status}")
    colour = _DOCTOR_COLOUR.get(status)
    if colour is None:                           # pragma: no cover - unknown code
        return status
    width = max(len(t(f"doctor.mark.{s}")) for s in _DOCTOR_COLOUR)
    return f"[{colour}]{label:<{width}}[/{colour}]"


@app.command("doctor")
def doctor_cmd() -> None:
    """Check every part of the setup and say what to do next."""
    from mining_dark import doctor

    _bootstrap()
    try:
        report = doctor.run()
    except ConfigError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan")
    table.add_column("")
    table.add_column(t("doctor.col.check"))
    table.add_column(t("doctor.col.state"))
    for check in report.checks:
        table.add_row(_doctor_mark(check.status), check.name, check.detail)
    console.print(table)

    for check in report.checks:
        if check.status != "ok" and check.fix:
            console.print(f"  [dim]{check.name}:[/dim] {check.fix}")

    if report.next_step:
        console.print(Panel(
            f"[bold cyan]{report.next_step}[/bold cyan]",
            box=box.ROUNDED, title=f"[cyan]{t('doctor.next_step')}[/cyan]",
        ))
    else:
        console.print(f"\n[bold green]{t('doctor.all_set')}[/bold green] "
                      f"{t('doctor.all_set_hint')} [cyan]mining-dark scan[/cyan]\n")

    # Non-zero only for real failures: a warning is "not finished yet", which
    # is the normal state halfway through setup and must not read as an error
    # in a script.
    if report.failed:
        raise typer.Exit(1)


# ----- utxo ------------------------------------------------------------------
@utxo_app.command("update")
def utxo_update(
    force: bool = typer.Option(False, "--force", "-f", help="Force re-import even if up to date"),
    file: Optional[Path] = typer.Option(None, "--file", help="Import from a local CSV file"),
    from_snapshot: bool = typer.Option(
        False, "--from-snapshot",
        help="Export from a loaded assumeutxo snapshot with the node stopped",
    ),
    ignore_node_errors: bool = typer.Option(
        False, "--ignore-node-errors",
        help="Export even if Bitcoin Core recently reported a corrupt database",
    ),
) -> None:
    """
    Rebuild the UTXO database from Bitcoin Core (via bitcoin-utxo-dump) or a CSV file.
    """
    _bootstrap()
    from mining_dark import bitcoin_node, utxo_updater

    if file:
        utxo_updater.update_from_file(file)
        return

    from mining_dark.utils.db_lock import DatabaseBusyError

    try:
        utxo_updater.update_from_node(
            force=force,
            from_snapshot=from_snapshot,
            ignore_node_errors=ignore_node_errors,
        )
    except bitcoin_node.BitcoinNodeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None
    except DatabaseBusyError as e:
        console.print(Panel(
            f"[bold yellow]{e}[/bold yellow]",
            box=box.ROUNDED, title=f"[yellow]{t('cli.db.busy')}[/yellow]",
        ))
        raise typer.Exit(1) from None
    except RuntimeError as e:
        # _reject_implausible_import and the dump guards raise these, and each
        # message already explains what to do.  A traceback would bury it.
        console.print(Panel(
            f"[bold red]{e}[/bold red]",
            box=box.ROUNDED, title=f"[red]{t('cli.utxo.refused')}[/red]",
        ))
        raise typer.Exit(1) from None


@utxo_app.command("status")
def utxo_status() -> None:
    """Show the UTXO database health (size, age, number of addresses)."""
    settings = _bootstrap()
    from mining_dark.utils.utxo_db import UTXODatabase

    rows = ("status", "addresses", "size", "updated", "source", "height")
    labels = {key: t(f"cli.utxo.{key}") for key in rows}
    # Padded to the longest label so the colons line up in either language.
    width = max(len(label) for label in labels.values())

    with UTXODatabase(settings.utxo.resolved_db_file()) as db:
        values = {
            "status": db.status,
            "addresses": f"{db.address_count:,}",
            "size": f"{db.db_size_mb:,.1f} MB",
            "updated": db.last_updated or "-",
            "source": db.source,
            "height": f"{db.block_height:,}" if db.block_height else "-",
        }
        console.print(f"[bold]{t('cli.utxo.title')}[/bold] {db.db_path}")
        for key in rows:
            console.print(f"  {labels[key]:<{width}} : {values[key]}")


# ----- node ------------------------------------------------------------------
@node_app.command("start")
def node_start(
    reindex: bool = typer.Option(
        False,
        "--reindex",
        help="Rebuild block index (needed after abrupt shutdown / corruption)",
    ),
    shallow_verify: bool = typer.Option(
        False,
        "--shallow-verify",
        help="Start with -checklevel=1, for a pruned node whose undo files are gone",
    ),
) -> None:
    """Start bitcoind against the project datadir (data/bitcoin-core/)."""
    from mining_dark import bitcoin_node

    _bootstrap()

    # A previous start that died on verification pruning made impossible is not
    # corruption, and Core's advice for it (-reindex) would discard a working
    # datadir.  Say so before the same start fails the same way again.
    if not shallow_verify and bitcoin_node.pruned_verify_failure():
        console.print(Panel(
            t("cli.node.verify_vs_prune_body"),
            box=box.ROUNDED, title=f"[yellow]{t('cli.node.verify_vs_prune')}[/yellow]",
        ))

    level = bitcoin_node.SHALLOW_CHECK_LEVEL if shallow_verify else None
    try:
        bitcoin_node.start(reindex=reindex, check_level=level)
    except bitcoin_node.BitcoinNodeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    console.print(
        f"[green]{t('cli.node.started')}[/green] {t('cli.node.started_at')} "
        f"[cyan]{paths.BITCOIN_CORE_DIR}[/cyan]"
        + (" [yellow](reindex mode)[/yellow]" if reindex else "")
        + (" [yellow](-checklevel=1)[/yellow]" if shallow_verify else "")
    )
    console.print(
        f"  {t('cli.node.follow')} [cyan]mining-dark node status[/cyan]  "
        f"({t('cli.node.or')} [cyan]mining-dark node cli getblockchaininfo[/cyan])"
    )


@node_app.command("stop")
def node_stop(
    timeout: float = typer.Option(60.0, "--timeout", "-t", help="Seconds to wait for clean exit"),
) -> None:
    """Stop bitcoind gracefully (via bitcoin-cli stop, waits for shutdown)."""
    from mining_dark import bitcoin_node

    _bootstrap()
    try:
        exited = bitcoin_node.stop(timeout=timeout)
    except bitcoin_node.BitcoinNodeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    if exited:
        console.print(f"[green]{t('cli.node.stopped_clean')}[/green]")
    else:
        console.print(
            f"[yellow]{t('cli.node.stop_timeout', seconds=f'{timeout:.0f}')}[/yellow]  "
            f"{t('cli.node.stop_timeout_hint')} "
            f"[cyan]mining-dark node status[/cyan]."
        )
        raise typer.Exit(1)


@node_app.command("status")
def node_status() -> None:
    """Show whether bitcoind is running and its current sync progress."""
    from mining_dark import bitcoin_node

    _bootstrap()
    running = bitcoin_node.is_running()

    console.print(f"[bold]{t('cli.status.datadir')}[/bold] {paths.BITCOIN_CORE_DIR}")
    console.print(
        f"[bold]{t('cli.status.process')}[/bold] "
        + (f"[green]{t('cli.status.running')}[/green]" if running
           else f"[red]{t('cli.status.stopped')}[/red]")
    )

    if not running:
        console.print(
            f"\n[dim]{t('cli.status.to_start')}[/dim] [cyan]mining-dark node start[/cyan]"
        )
        return

    info = bitcoin_node.getblockchaininfo()
    if info is None:
        console.print(f"\n[yellow]{t('cli.status.no_rpc')}[/yellow]")
        return

    progress_pct = info.get("verificationprogress", 0) * 100
    blocks       = info.get("blocks", 0)
    headers      = info.get("headers", 0)
    chain        = info.get("chain", "?")
    pruned       = info.get("pruned", False)
    ibd          = info.get("initialblockdownload", False)
    size_bytes   = info.get("size_on_disk", 0)

    labels = {key: t(f"cli.status.{key}") for key in ("chain", "blocks", "sync", "size")}
    width = max(len(label) for label in labels.values())
    kind = t("cli.status.pruned") if pruned else t("cli.status.full")

    console.print(f"[bold]{labels['chain']:<{width}}[/bold] {chain}  ({kind})")
    console.print(f"[bold]{labels['blocks']:<{width}}[/bold] {blocks:,} / {headers:,}")
    console.print(
        f"[bold]{labels['sync']:<{width}}[/bold] {progress_pct:.4f}%  "
        + ("[yellow](IBD)[/yellow]" if ibd
           else f"[green]({t('cli.status.caught_up')})[/green]")
    )
    console.print(f"[bold]{labels['size']:<{width}}[/bold] {size_bytes / 1e9:.2f} GB")

    # assumeutxo: while the background sync runs there are two chainstates and
    # the numbers above describe the snapshot one (the tip), not the validated one.
    snap = bitcoin_node.snapshot_status()
    if snap and snap["active"]:
        done = snap["background_blocks"]
        target = snap["tip_blocks"] or 1
        snap_labels = [t(f"cli.status.snapshot_{key}")
                       for key in ("tip", "validated", "chainstate")]
        snap_width = max(len(label) for label in snap_labels)
        console.print(
            f"\n[bold]{t('cli.status.snapshot_active')}[/bold] "
            f"[green]{t('cli.status.active')}[/green]\n"
            f"  {snap_labels[0]:<{snap_width}} : {snap['tip_blocks']:,}\n"
            f"  {snap_labels[1]:<{snap_width}} : {done:,}  ({done / target * 100:.2f}%)\n"
            f"  {snap_labels[2]:<{snap_width}} : "
            f"[cyan]{bitcoin_node.active_chainstate_dir().name}[/cyan]"
        )
        console.print(f"  [dim]{t('cli.status.snapshot_note')}[/dim]")

    # An aborted loadtxoutset leaves a directory Core silently ignores, so the
    # only hint left is gigabytes missing from the disk.  Say it out loud.
    if bitcoin_node.snapshot_dir_state() == "orphaned":
        console.print(Panel(
            t("cli.status.orphaned_body", path=paths.SNAPSHOT_CHAINSTATE_DIR),
            box=box.ROUNDED, title=f"[yellow]{t('cli.status.orphaned_title')}[/yellow]",
        ))

    if progress_pct >= 99.99:
        console.print(
            f"\n[green]{t('cli.status.synced')}[/green]  {t('cli.run')} "
            f"[cyan]mining-dark utxo update[/cyan]"
        )


def _offer_snapshot_cleanup(file: Path, remove: Optional[bool]) -> None:
    """
    Offer to delete the .dat once it has been loaded.

    Core streams the file into `chainstate_snapshot/` once and never reads it
    again, and it refuses a second `loadtxoutset` in the same datadir - so a
    loaded snapshot leaves ~9 GB with nothing left to do.  Gated on the load
    having actually completed: while it hasn't, that file is the only way to
    retry, and deleting it would cost another full download.
    """
    from mining_dark import bitcoin_node

    if bitcoin_node.snapshot_dir_state() != "loaded" or not file.is_file():
        return

    size_gb = file.stat().st_size / 1e9

    size = f"{size_gb:.1f}"

    if remove is None:
        if not sys.stdin.isatty():
            console.print(
                f"\n[dim]{t('cli.snap.cleanup_note', size=size, path=file)}[/dim]"
            )
            return
        remove = typer.confirm(
            "\n" + t("cli.snap.cleanup_ask", size=size),
            default=False,
        )

    if not remove:
        console.print(f"[dim]{t('cli.snap.cleanup_kept', path=file)}[/dim]")
        return

    try:
        file.unlink()
    except OSError as e:
        console.print(f"[yellow]{t('cli.snap.cleanup_failed', path=file, error=e)}[/yellow]")
        return
    console.print(f"[green]{t('cli.snap.cleanup_freed', size=size)}[/green] {file}")


@node_app.command("download-snapshot")
def node_download_snapshot() -> None:
    """
    Download the assumeutxo snapshot, resuming whatever is already on disk.

    Roughly 9 GB.  Interrupting is safe: the partial file stays and the next
    run continues from where it stopped rather than starting over.
    """
    from rich.progress import (
        BarColumn,
        DownloadColumn,
        Progress,
        TextColumn,
        TimeRemainingColumn,
        TransferSpeedColumn,
    )

    from mining_dark import snapshot as snap

    _bootstrap()

    target = snap.snapshot_path()
    expected = snap.remote_size(snap.mirror_urls()[0])

    if snap.is_complete(target, expected):
        console.print(f"[green]{t('cli.snap.already')}[/green] {target}")
        console.print(f"{t('cli.snap.load_with')} [cyan]mining-dark node snapshot[/cyan]")
        return

    already = snap.local_size(target)
    if already:
        console.print(
            f"[cyan]{t('cli.snap.resuming', done=f'{already / 1e9:.2f}')}[/cyan] "
            + t("cli.snap.of", total=f"{expected / 1e9:.2f}") + "\n"
        )

    progress = Progress(
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=console,
    )

    with progress:
        task = progress.add_task(t("cli.snap.downloading"), total=expected or None)

        def _tick(done: int, total: int) -> None:
            progress.update(task, completed=done, total=total or None)

        try:
            path = snap.download(on_progress=_tick)
        except snap.SnapshotError as exc:
            console.print(Panel(
                f"[bold red]{exc}[/bold red]",
                box=box.ROUNDED, title=f"[red]{t('cli.snap.download_failed')}[/red]",
            ))
            raise typer.Exit(1) from None
        except KeyboardInterrupt:
            done = f"{snap.local_size(target) / 1e9:.2f}"
            console.print(
                f"\n[yellow]{t('cli.snap.interrupted', done=done)}[/yellow] "
                + t("cli.snap.interrupted_hint")
            )
            raise typer.Exit(130) from None

    console.print(f"\n[green]{t('cli.snap.downloaded')}[/green] {path}")
    console.print(f"{t('cli.snap.load_with')} [cyan]mining-dark node snapshot[/cyan]")


@node_app.command("snapshot")
def node_snapshot(
    file: Optional[Path] = typer.Argument(
        None, help="Path to the .dat (default: the one downloaded to data/snapshots/)"
    ),
    remove_dat: Optional[bool] = typer.Option(
        None, "--remove-dat/--keep-dat",
        help="Delete the .dat once it is loaded (default: ask)",
    ),
) -> None:
    """
    Load an assumeutxo UTXO snapshot to skip most of the initial block download.

    Bitcoin Core checks the file against a hash compiled into its own binary, so
    a tampered snapshot is rejected - the download source doesn't need to be
    trusted.  Blocks for tens of minutes to a few hours.
    """
    from mining_dark import bitcoin_node
    from mining_dark import snapshot as snap

    _bootstrap()

    file = file or snap.snapshot_path()

    # Checked before the load rather than during: loadtxoutset on a truncated
    # file fails hours in, after Core has already built a chainstate directory
    # it then throws away.
    expected = snap.remote_size(snap.mirror_urls()[0])
    if not file.exists():
        console.print(Panel(
            f"[bold red]{t('cli.snap.absent', path=file)}[/bold red]\n\n"
            f"{t('cli.snap.absent_hint')} "
            "[cyan]mining-dark node download-snapshot[/cyan]",
            box=box.ROUNDED, title=f"[red]{t('cli.snap.absent_title')}[/red]",
        ))
        raise typer.Exit(1)
    if expected and not snap.is_complete(file, expected):
        detail = t("cli.snap.partial_detail",
                   done=f"{snap.local_size(file) / 1e9:.2f}",
                   total=f"{expected / 1e9:.2f}")
        console.print(Panel(
            f"[bold red]{t('cli.snap.partial')}[/bold red] {detail}\n\n"
            f"{t('cli.snap.partial_hint')} "
            "[cyan]mining-dark node download-snapshot[/cyan]",
            box=box.ROUNDED, title=f"[red]{t('cli.snap.partial_title')}[/red]",
        ))
        raise typer.Exit(1)

    console.print(
        f"[bold cyan]{t('cli.snap.loading')}[/bold cyan] {file}\n"
        f"[dim]{t('cli.snap.loading_note')}[/dim]\n"
    )

    try:
        result = bitcoin_node.load_snapshot(file)
    except bitcoin_node.BitcoinNodeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]{t('cli.snap.loaded')}[/green]")
    for key in ("coins_loaded", "base_height", "base_hash", "tip_hash"):
        if key in result:
            value = result[key]
            console.print(f"  {key:<13}: [cyan]{value:,}[/cyan]"
                          if isinstance(value, int) else f"  {key:<13}: [cyan]{value}[/cyan]")

    _offer_snapshot_cleanup(file, remove_dat)

    console.print(
        f"\n[dim]{t('cli.snap.background_note')}[/dim] "
        "[cyan]mining-dark node status[/cyan]"
    )


@node_app.command(
    "cli",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def node_cli(ctx: typer.Context) -> None:
    """
    Passthrough to `bitcoin-cli`.  Examples:

      mining-dark node cli getblockchaininfo
      mining-dark node cli getnetworkinfo
      mining-dark node cli -help
    """
    from mining_dark import bitcoin_node

    _bootstrap()
    try:
        exit_code = bitcoin_node.run_cli_passthrough(ctx.args)
    except bitcoin_node.BitcoinNodeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    raise typer.Exit(exit_code)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
