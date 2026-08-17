"""
Imports Bitcoin Core's UTXO set into the local SQLite database.

Pipeline:
    Bitcoin Core (chainstate/)  ->  bitcoin-utxo-dump (CSV)  ->  UTXODatabase (SQLite)

The Bitcoin Core datadir lives at `data/bitcoin-core/` inside the project, so
every call goes through `bitcoin-cli -datadir=<...>` to stay pointed there.
"""

from __future__ import annotations

import csv
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)

from mining_dark import bitcoin_node, paths
from mining_dark.i18n import t
from mining_dark.utils import db_lock
from mining_dark.utils.utxo_db import UTXODatabase

console = Console()

_BATCH_SIZE = 50_000

# 21 million BTC.  Anything larger is corruption, not an amount.
_MAX_MONEY_SATOSHIS = 21_000_000 * 100_000_000

# Stopping a mainnet node means flushing the whole coins cache, which takes
# considerably longer than an idle shutdown.
_NODE_STOP_TIMEOUT = 600.0


#: verificationprogress is a time-weighted estimate that asymptotes towards 1,
#: so it clears this threshold well before the node reaches the tip.
_MIN_VERIFICATION_PROGRESS = 0.9999

#: How far back to look for the node complaining about its own database before
#: trusting a snapshot export.  Wide on purpose: a corrupted chainstate does not
#: heal on its own, so a complaint from a day ago still describes the files
#: sitting there now.
_SNAPSHOT_TRUST_WINDOW_S = 86_400.0


def _sync_shortfall(info: dict) -> str:
    """
    Why the node is not ready to be dumped, or "" when it is.

    Progress alone is not enough: a node hours behind the tip still reports
    0.9999+, and dumping there produces a database that silently misses the most
    recent coins.  `blocks == headers` is the check that actually pins the tip,
    and initialblockdownload is Core's own verdict.
    """
    blocks = int(info.get("blocks", 0))
    headers = int(info.get("headers", 0))

    if info.get("initialblockdownload", False):
        return t("utxo.sync.ibd")

    if headers and blocks < headers:
        return t("utxo.sync.behind", behind=f"{headers - blocks:,}")

    if float(info.get("verificationprogress", 0)) < _MIN_VERIFICATION_PROGRESS:
        return t("utxo.sync.verifying")

    return ""


def _resolved_db_file() -> Path:
    """
    The database path the scanner actually reads.

    Every reader goes through `settings.utxo.resolved_db_file()`, but the writer
    used to hardcode `paths.UTXO_DB_FILE`.  With `utxo.db_file` set in
    config.yaml the two diverged: each update reported success while the scan
    kept querying a different - and increasingly stale - file.
    """
    from mining_dark.config.settings import load_settings

    return load_settings().utxo.resolved_db_file()


# ----- Bitcoin Core helpers --------------------------------------------------
def check_bitcoin_core() -> dict:
    """
    Return getblockchaininfo if Bitcoin Core is up, or {"error": "..."} if the
    binaries are missing or the node can't be reached.
    """
    missing = bitcoin_node.require_binaries("bitcoin-cli", "bitcoin-utxo-dump")
    if missing:
        return {"error": t("utxo.err.missing_binary", name=missing)}

    info = bitcoin_node.getblockchaininfo()
    if info is None:
        return {"error": t("utxo.err.core_down", datadir=paths.BITCOIN_CORE_DIR)}
    return info


def _run_utxo_dump(output_path: Path) -> None:
    """
    Run bitcoin-utxo-dump against the chainstate that matches the node's tip.

    Which directory that is depends on assumeutxo: while a snapshot background
    sync is running the tip lives in `chainstate_snapshot/`, not `chainstate/`.
    require_dumpable_chainstate() resolves it, and refuses the states where no
    directory holds a coherent UTXO set - dumping those would build a database
    from a partial snapshot without anything looking wrong.
    """
    chainstate = bitcoin_node.require_dumpable_chainstate()
    # -p2pkaddresses converts the raw pubkey in a P2PK output into its address.
    # Without it those rows come back with an empty `address` and _parse_csv drops
    # them, so the Satoshi-era P2PK coins never reach the database - while the
    # scanner still derives and looks up p2pkh_uncompressed for every key, which
    # exists precisely to match them (see WalletKeys in core/wallet.py).  The
    # result is a whole output class the scanner can never find, with nothing
    # anywhere reporting a problem.
    cmd = [
        "bitcoin-utxo-dump",
        "-nowarnings",
        "-p2pkaddresses",
        "-db", str(chainstate),
        "-f", "address,amount",
        "-o", str(output_path),
    ]

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    )
    with progress:
        task = progress.add_task(f"[cyan]{t('utxo.exporting')}", total=None)
        result = subprocess.run(cmd, capture_output=True, text=True)
        progress.update(task, description=f"[green]{t('utxo.export_done')}")

    if result.returncode != 0:
        raise RuntimeError(t(
            "utxo.dump_failed",
            code=result.returncode,
            chainstate=chainstate,
            datadir=paths.BITCOIN_CORE_DIR,
            error=result.stderr,
        ))

    # The exit code above guards nothing: bitcoin-utxo-dump returns 0 for a
    # missing -db directory and for one that is not a LevelDB at all, writing
    # either no file or a header-only CSV.  The output is the only evidence.
    if not output_path.exists():
        raise RuntimeError(t(
            "utxo.dump_no_csv",
            chainstate=chainstate,
            output=f"{result.stdout}\n{result.stderr}",
        ))

    console.print("  [dim]" + t(
        "utxo.csv_written",
        name=output_path.name,
        size=f"{output_path.stat().st_size / 1e6:.0f}",
    ) + "[/dim]")


# ----- Import CSV -> SQLite ---------------------------------------------------
def _parse_csv(src: Path, db: UTXODatabase, progress: Progress, task_id) -> int:
    """Import the bitcoin-utxo-dump CSV: columns are address,amount (sats per UTXO)."""
    total = 0
    batch: list[tuple[str, int]] = []

    with open(src, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # DictReader fills missing fields with None, not with the default
            # passed to .get() - a truncated last line (killed dump, full disk)
            # used to abort the whole import with a TypeError.
            address = (row.get("address") or "").strip()
            if not address:
                continue
            try:
                satoshis = int(row.get("amount") or 0)
            except (TypeError, ValueError):
                continue
            # Above the total supply the value cannot be a real amount, and
            # letting it through overflows SQLite's INTEGER on the upsert.
            if satoshis <= 0 or satoshis > _MAX_MONEY_SATOSHIS:
                continue

            batch.append((address, satoshis))
            total += 1

            if len(batch) >= _BATCH_SIZE:
                db.batch_insert(batch)
                db.commit()
                batch.clear()
                progress.update(
                    task_id,
                    description=f"[cyan]{t('utxo.importing_n', count=f'{total:,}')}",
                )

    if batch:
        db.batch_insert(batch)
        db.commit()

    return total


# Below this fraction of the previous size, a rebuild is treated as a failed
# export rather than a legitimate shrink.  The real UTXO set never halves.
_MIN_SHRINK_RATIO = 0.5


def _discard_stale_temps(target_db: Path) -> None:
    """
    Drop what an earlier rebuild left behind, before this one writes anything.

    The normal cleanup lives in a `finally`, which never runs after SIGKILL, a
    closed terminal or a power cut - so the temporaries outlive the run that
    made them.  Two consequences, both seen: several hundred MB sitting there
    for good, and a half-built `.tmp.db` that the panel read as this rebuild's
    import progress, freezing the bar at the byte count the dead run reached.

    Safe by construction - the rebuild lock is already held, so no other
    rebuild can own these - and it only ever removes temporaries, never the
    live database beside them.
    """
    from mining_dark.utils.utxo_db import UTXODatabase

    tmp_db = target_db.with_suffix(".tmp.db")
    leftovers = [p for p in (paths.UTXO_TMP_CSV, tmp_db) if p.exists()]
    if not leftovers:
        return

    freed = sum(_file_size(p) for p in leftovers)
    console.print(
        f"[dim]{t('utxo.discarding_temps', size=f'{freed / 1e9:,.2f}')}[/dim]"
    )
    paths.UTXO_TMP_CSV.unlink(missing_ok=True)
    UTXODatabase._remove_with_sidecars(tmp_db)


def _file_size(path: Path) -> int:
    """A file's size, or 0 if it is gone - one stat(), never exists()-then-stat()."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _reject_implausible_import(unique_count: int, previous_count: int) -> None:
    """Refuse to replace a working database with an obviously broken import."""
    if unique_count == 0:
        raise RuntimeError(t("utxo.import_empty"))

    if previous_count and unique_count < previous_count * _MIN_SHRINK_RATIO:
        raise RuntimeError(t(
            "utxo.import_shrunk",
            count=f"{unique_count:,}",
            previous=f"{previous_count:,}",
            pct=f"{(1 - _MIN_SHRINK_RATIO) * 100:.0f}",
        ))


def do_import(
    src: Path,
    source_label: str,
    block_height: int = 0,
    db_file: Path | None = None,
) -> None:
    if not src.exists() or src.stat().st_size == 0:
        raise RuntimeError(t("utxo.csv_invalid", path=src))

    target_db = db_file if db_file is not None else _resolved_db_file()

    with db_lock.rebuilding(target_db):
        _do_import_locked(src, source_label, block_height, target_db)


def _do_import_locked(
    src: Path,
    source_label: str,
    block_height: int,
    target_db: Path,
) -> None:
    """The body of do_import, with the rebuild lock already held."""
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        console=console,
        expand=True,
    )

    # What the live database holds right now, to compare against below.
    with UTXODatabase(target_db) as current:
        try:
            previous_count = current.address_count if current.is_ready else 0
        except (TypeError, ValueError):
            previous_count = 0

    with progress:
        task = progress.add_task(f"[cyan]{t('utxo.importing')}", total=None)
        db = UTXODatabase.create(target_db)
        tmp_db = db.db_path          # finalize() renames this onto the live file
        try:
            _parse_csv(src, db, progress, task)
            progress.update(task, description=f"[cyan]{t('utxo.counting')}")
            unique_count = db.count_addresses()

            # finalize() replaces the live database, and rebuilding it costs
            # hours.  bitcoin-utxo-dump exits 0 even when it produced nothing,
            # so an empty or wildly shrunken result is the only signal that
            # something went wrong - refuse it while the old data still exists.
            _reject_implausible_import(unique_count, previous_count)

            progress.update(task, description=f"[green]{t('utxo.import_done')}")

            now = datetime.now(timezone.utc)
            db.set_meta("last_updated", now.isoformat())
            db.set_meta("source_date", now.strftime("%Y-%m-%d"))
            db.set_meta("source", source_label)
            db.set_meta("address_count", str(unique_count))
            if block_height:
                db.set_meta("block_height", str(block_height))

            # What this rebuild actually cost, so the next one can predict
            # itself instead of being guessed at from the chainstate size.
            # That guess was out by 4x - it read the 12.66 GB chainstate as the
            # size of a database that came out at 3.12 GB - which left the
            # progress bar finishing the import phase at about a quarter full.
            for key, size in (("last_csv_bytes", _file_size(src)),
                              ("last_db_bytes", _file_size(tmp_db))):
                if size:
                    db.set_meta(key, str(size))
            db.finalize()
        finally:
            # On any failure the half-built database is dead weight - on
            # mainnet, several GB of it sitting next to the live one.  After a
            # successful finalize the rename already moved it away.
            db.close()
            UTXODatabase._remove_with_sidecars(tmp_db)

    rows = [
        (t("utxo.imported.addresses"), f"{unique_count:,}"),
        (t("utxo.imported.source"), source_label),
        (t("utxo.imported.path"), str(target_db)),
        (t("utxo.imported.size"), f"{target_db.stat().st_size / 1e9:.2f} GB"),
    ]
    width = max(len(label) for label, _ in rows)
    console.print(Panel(
        f"[bold green]{t('utxo.imported')}[/bold green]\n\n"
        + "\n".join(f"  {label:<{width}} : [cyan]{value}[/cyan]" for label, value in rows),
        box=box.ROUNDED,
    ))


def _snapshot_hint() -> str:
    """Point at the offline export when the node is down but the snapshot is not."""
    if bitcoin_node.snapshot_dir_state() != "loaded":
        return ""
    return t("utxo.snap.hint")


def _announce_snapshot_export(ignore_node_errors: bool = False) -> int:
    """
    Check that an offline snapshot export is actually justified, and explain it.

    Returns the snapshot's block height when it can be determined, 0 otherwise.
    """
    state = bitcoin_node.snapshot_dir_state()
    if state != "loaded":
        console.print(Panel(
            f"[bold red]{t('utxo.snap.none', state=state)}[/bold red]",
            box=box.ROUNDED, title=f"[red]{t('utxo.snap.none_title')}[/red]",
        ))
        raise SystemExit(1)

    if bitcoin_node.is_running():
        console.print(Panel(
            f"[bold red]{t('utxo.snap.node_up')}[/bold red]",
            box=box.ROUNDED, title=f"[red]{t('utxo.snap.node_up_title')}[/red]",
        ))
        raise SystemExit(1)

    # base_blockhash proves the snapshot was whole when it was loaded, not that
    # it still is: Core keeps applying blocks to that same LevelDB afterwards,
    # and an unclean shutdown can leave it inconsistent.  The node's own last
    # word on the matter is in debug.log, and this path never asked for it.
    complaint = bitcoin_node.last_startup_error(max_age_s=_SNAPSHOT_TRUST_WINDOW_S)
    if complaint and not ignore_node_errors:
        console.print(Panel(
            f"[bold red]{t('utxo.snap.suspect', complaint=complaint)}[/bold red]",
            box=box.ROUNDED, title=f"[red]{t('utxo.snap.suspect_title')}[/red]",
        ))
        raise SystemExit(1)
    if complaint:
        console.print(
            f"[yellow]{t('utxo.snap.ignored')}[/yellow] {complaint}"
        )

    console.print(Panel(
        f"[bold cyan]{t('utxo.snap.exporting')}[/bold cyan]",
        box=box.ROUNDED, title=f"[cyan]{t('utxo.snap.title')}[/cyan]",
    ))

    # No RPC to ask offline, so the height comes from what the node last logged.
    height = bitcoin_node.snapshot_height_from_log()
    if height:
        console.print(f"  [dim]{t('utxo.snap.height', height=f'{height:,}')}[/dim]\n")
    return height


def update_from_node(
    force: bool = False,
    from_snapshot: bool = False,
    ignore_node_errors: bool = False,
) -> None:
    """
    Full pipeline: bitcoin-utxo-dump -> CSV -> SQLite.

    `from_snapshot` exports from a loaded assumeutxo snapshot with the node
    down, skipping the RPC sync checks.  That is safe precisely because those
    checks exist to prove the chainstate on disk is complete at the tip, and a
    loaded snapshot proves it another way: Core writes `base_blockhash` only
    after deserialising every coin and matching the set against its hardcoded
    hash.  It is the way out when the node cannot start at all - a chainstate
    corrupted by an unclean shutdown, say - while the snapshot itself is fine.
    """
    paths.ensure_data_dirs()
    target_db = _resolved_db_file()

    # Ask about the database the scanner actually reads.  Checking the default
    # path instead meant that, with utxo.db_file configured, a stale database
    # could be refused an update because an unrelated file looked fresh.
    with UTXODatabase(target_db) as db_check:
        if not force and not db_check.needs_update and db_check.is_ready:
            console.print(
                f"[green]{t('utxo.up_to_date')}[/green] "
                + t("utxo.up_to_date_detail", days=db_check.age_days)
            )
            return

    # Taken before the node is stopped and before the export, not down inside
    # do_import.  Held only around the import, it let a second update stop the
    # node, spend half an hour writing the same CSV path as the first, and be
    # refused at the very end - two exports interleaved in one file, and an
    # import that would have doubled every balance the overlap touched.
    with db_lock.rebuilding(target_db):
        _update_from_node_locked(force, from_snapshot, target_db, ignore_node_errors)


def _update_from_node_locked(
    force: bool,
    from_snapshot: bool,
    target_db: Path,
    ignore_node_errors: bool = False,
) -> None:
    """The body of update_from_node, with the rebuild lock already held."""

    # Preflight the chainstate layout before touching the node: it is a couple
    # of stat() calls, and it turns "half an hour of export, then a wrong
    # database" into an immediate, actionable error.
    try:
        bitcoin_node.require_dumpable_chainstate()
    except bitcoin_node.BitcoinNodeError as e:
        console.print(Panel(
            f"[bold red]{e}[/bold red]",
            box=box.ROUNDED, title=f"[red]{t('utxo.chainstate_bad')}[/red]",
        ))
        raise SystemExit(1) from None

    if from_snapshot:
        blocks = _announce_snapshot_export(ignore_node_errors)
        source_label = "bitcoin_core_assumeutxo"
        return _dump_and_import(source_label, blocks, target_db, node_was_running=False)

    console.print(f"[bold cyan]{t('utxo.checking_core')}[/bold cyan]")
    info = check_bitcoin_core()

    if "error" in info:
        console.print(Panel(
            f"[bold red]{info['error']}[/bold red]{_snapshot_hint()}",
            box=box.ROUNDED, title=f"[red]{t('utxo.err.title')}[/red]",
        ))
        raise SystemExit(1)

    progress_sync = info.get("verificationprogress", 0)
    blocks        = info.get("blocks", 0)
    headers       = info.get("headers", 0)
    chain         = info.get("chain", "?")

    info_rows = [
        (t("utxo.net"), f"[cyan]{chain}[/cyan]"),
        (t("utxo.blocks"), f"[cyan]{blocks:,}[/cyan] / {headers:,}"),
        (t("utxo.synced_pct"), f"[cyan]{progress_sync * 100:.2f}%[/cyan]"),
    ]
    info_width = max(len(label) for label, _ in info_rows)
    console.print("\n".join(
        f"  {label:<{info_width}} : {value}" for label, value in info_rows
    ))

    reason = _sync_shortfall(info)
    if reason:
        short_rows = [
            (t("utxo.sync.progress"), f"[cyan]{progress_sync * 100:.2f}%[/cyan]"),
            (t("utxo.sync.blocks"), f"[cyan]{blocks:,} / {headers:,}[/cyan]"),
        ]
        short_width = max(len(label) for label, _ in short_rows)
        console.print(Panel(
            f"[bold yellow]{t('utxo.sync.incomplete')}[/bold yellow]\n\n"
            f"  {reason}\n"
            + "\n".join(f"  {label:<{short_width}} : {value}"
                        for label, value in short_rows)
            + f"\n\n{t('utxo.sync.wait')}",
            box=box.ROUNDED,
            title=f"[yellow]{t('utxo.sync.incomplete_title')}[/yellow]",
        ))
        raise SystemExit(1)

    console.print(f"  [green]{t('utxo.synced_go')}[/green]\n")

    # With assumeutxo the tip is served from chainstate_snapshot/ until the
    # background validation catches up.  The UTXO set there is complete and was
    # checked against Core's hardcoded hash, so it is safe to dump - but say so,
    # since the node is not yet fully validated from genesis.
    source_label = "bitcoin_core"
    if bitcoin_node.snapshot_in_progress():
        source_label = "bitcoin_core_assumeutxo"
        console.print(Panel(
            f"[yellow]{t('utxo.snap.background')}[/yellow]",
            box=box.ROUNDED, title=f"[yellow]{t('utxo.snap.title')}[/yellow]",
        ))

    # bitcoin-utxo-dump reads the chainstate LevelDB straight off disk, and a
    # running node only flushes its coins cache when that cache fills up.  With
    # the node up, the directory can therefore lag the tip by an unbounded
    # amount - on a fresh regtest chain it reads as zero UTXOs, and the dump
    # still exits 0.  Nothing downstream can tell that apart from a real empty
    # set, so the node has to be stopped first; shutdown is what flushes it.
    node_was_running = bitcoin_node.is_running()
    if node_was_running:
        console.print(
            f"[bold cyan]{t('utxo.stopping_core')}[/bold cyan] "
            f"[dim]{t('utxo.stopping_core_why')}[/dim]"
        )
        if not bitcoin_node.stop(timeout=_NODE_STOP_TIMEOUT):
            console.print(Panel(
                f"[bold red]{t('utxo.stop_failed')}[/bold red]",
                box=box.ROUNDED, title=f"[red]{t('utxo.stop_failed_title')}[/red]",
            ))
            raise SystemExit(1)
        console.print(f"  [green]{t('utxo.node_stopped')}[/green]\n")

    _dump_and_import(source_label, blocks, target_db, node_was_running=node_was_running)


def _dump_and_import(
    source_label: str,
    blocks: int,
    target_db: Path,
    *,
    node_was_running: bool,
) -> None:
    """
    Export the chainstate to CSV, import it, and put the node back as found.

    The rebuild lock is already held by the caller, so this goes through
    `_do_import_locked` rather than `do_import` - taking the same flock twice
    from one process would refuse itself.
    """
    tmp_csv = paths.UTXO_TMP_CSV
    _discard_stale_temps(target_db)
    imported = False
    try:
        _run_utxo_dump(tmp_csv)
        _do_import_locked(tmp_csv, source_label, blocks, target_db)
        imported = True
    finally:
        # A refused import tells the user to retry with `utxo update --file`,
        # so the CSV it refers to has to survive; half an hour of export is too
        # much to throw away on the way to reporting a problem.
        if imported:
            tmp_csv.unlink(missing_ok=True)
        elif tmp_csv.exists():
            console.print(f"\n[dim]{t('utxo.csv_kept', path=tmp_csv)}[/dim]")
        if node_was_running:
            console.print(f"\n[cyan]{t('utxo.restarting')}[/cyan]")
            try:
                bitcoin_node.start()
            except bitcoin_node.BitcoinNodeError as exc:
                console.print(
                    f"[yellow]{t('utxo.restart_failed', error=exc)}[/yellow]"
                )


def update_from_file(file: Path) -> None:
    paths.ensure_data_dirs()
    do_import(file, source_label="local_file")
