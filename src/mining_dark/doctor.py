"""
One command that answers "is this working?".

The setup has four moving parts - binaries, node config, the snapshot, the UTXO
database - and each reports its own health somewhere else: `node status` covers
the process, `utxo status` the database, the config nowhere at all.  Answering
the question meant knowing which of them to ask, and in what order.

So this asks all of them and says what to do next.  Nothing is started or
stopped, and the only thing written anywhere is a probe file in the found-
wallets directory, removed immediately - see `_found_wallets` for why asking
that question any other way does not answer it.

The checks return data, not printed text - `mining_dark.cli` renders them - so
the whole thing is testable without a terminal.  The `name`, `detail` and `fix`
they carry are already translated, in whichever language was active when `run`
was called; `status` is a stable code, so that is what tests and scripts read.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mining_dark.i18n import t

#: A check that passed, one worth mentioning, and one that blocks progress.
OK = "ok"
WARN = "warn"
FAIL = "fail"

#: Below this the block+undo budget cannot hold the tip and the historical
#: blocks an assumeutxo background sync downloads at the same time.  At 2 GiB
#: Core evicted the tip's undo files and then reported the missing data as a
#: corrupt database - see `bitcoin_node.pruned_verify_failure`.
MIN_PRUNE_MIB = 10_000

#: How far behind the peers a node that believes it is synced may fall before
#: `_chain_progress` calls it stalled.  Six blocks is roughly an hour, which
#: covers ordinary propagation lag and a burst of fast blocks without letting a
#: genuinely stuck node hide - the one this check exists for was 848 behind.
MAX_BLOCKS_BEHIND = 6


@dataclass(frozen=True)
class Check:
    """One thing that was looked at, and what to do if it is not right."""

    name: str
    status: str
    detail: str
    fix: str = ""


@dataclass(frozen=True)
class Report:
    checks: list = field(default_factory=list)
    #: The single thing to do next, or "" when there is nothing to do.
    next_step: str = ""

    @property
    def failed(self) -> list:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def warned(self) -> list:
        return [c for c in self.checks if c.status == WARN]

    @property
    def healthy(self) -> bool:
        return not self.failed


def _binaries() -> Check:
    from mining_dark import bitcoin_node

    missing = [
        name for name in ("bitcoind", "bitcoin-cli", "bitcoin-utxo-dump")
        if bitcoin_node.require_binaries(name)
    ]
    name = t("doctor.name.binaries")
    if missing:
        return Check(name, FAIL, t("doctor.binaries.missing", names=", ".join(missing)),
                     "bash scripts/setup_bitcoin_core.sh")
    return Check(name, OK, t("doctor.binaries.ok"))


def _node_config() -> Check:
    from mining_dark import bitcoin_node, paths

    name = t("doctor.name.conf")
    values = bitcoin_node.conf_values()
    if not values:
        return Check(name, FAIL,
                     t("doctor.conf.missing", dir=paths.BITCOIN_CORE_DIR),
                     "bash scripts/setup_bitcoin_core.sh")

    prune = bitcoin_node.prune_target_mib()
    if prune == 0:
        return Check(name, OK, t("doctor.conf.full_node"))

    notes = [f"prune={prune} MiB"]
    if prune < MIN_PRUNE_MIB:
        return Check(
            name, WARN,
            t("doctor.conf.prune_tight", prune=prune),
            t("doctor.conf.prune_tight_fix", min=MIN_PRUNE_MIB,
              path=paths.BITCOIN_CORE_DIR / "bitcoin.conf"),
        )

    level = values.get("checklevel")
    if level is None or int(level or 3) > 2:
        notes.append(f"checklevel={level or 3}")
        return Check(name, WARN, "  ·  ".join(notes), t("doctor.conf.checklevel_fix"))

    notes.append(f"checklevel={level}")
    return Check(name, OK, "  ·  ".join(notes))


def _snapshot_file() -> Check:
    from mining_dark import snapshot as snap

    name = t("doctor.name.snapshot_file")
    path = snap.snapshot_path()
    have = snap.local_size(path)
    if not have:
        return Check(name, OK, t("doctor.snapshot_file.absent"))
    return Check(name, OK,
                 t("doctor.snapshot_file.present", size=f"{have / 1e9:.2f}", dir=path.parent))


def _snapshot_state() -> Check:
    from mining_dark import bitcoin_node, paths

    name = t("doctor.name.snapshot_state")
    try:
        state = bitcoin_node.snapshot_dir_state()
    except OSError as exc:                       # pragma: no cover - unreadable
        return Check(name, FAIL, str(exc))

    if state == "loaded":
        return Check(name, OK, t("doctor.snapshot.loaded"))
    if state == "loading":
        return Check(name, WARN, t("doctor.snapshot.loading"),
                     t("doctor.snapshot.loading_fix"))
    if state == "orphaned":
        return Check(
            name, FAIL,
            t("doctor.snapshot.orphaned"),
            t("doctor.snapshot.orphaned_fix", path=paths.SNAPSHOT_CHAINSTATE_DIR),
        )
    return Check(name, OK, t("doctor.snapshot.none"))


def _node_process() -> Check:
    from mining_dark import bitcoin_node

    name = t("doctor.name.core")
    if bitcoin_node.pruned_verify_failure():
        return Check(name, FAIL, t("doctor.core.prune_verify"),
                     t("doctor.core.prune_verify_fix"))

    error = bitcoin_node.last_startup_error()
    if error:
        return Check(name, FAIL, error, t("doctor.core.see_debug_log"))

    if not bitcoin_node.is_running():
        return Check(name, WARN, t("doctor.core.stopped"), "mining-dark node start")

    info = bitcoin_node.getblockchaininfo()
    if info is None:
        return Check(name, WARN, t("doctor.core.no_rpc"), t("doctor.core.no_rpc_fix"))

    blocks, headers = int(info.get("blocks", 0)), int(info.get("headers", 0))
    behind = max(headers - blocks, 0)
    detail = t("doctor.core.blocks", blocks=f"{blocks:,}", headers=f"{headers:,}")
    if info.get("initialblockdownload") or behind:
        return Check(name, WARN,
                     t("doctor.core.behind", detail=detail, behind=f"{behind:,}"),
                     t("doctor.core.behind_fix"))
    return Check(name, OK, t("doctor.core.at_tip", detail=detail))


def _chain_progress() -> Check:
    """
    Is the node actually keeping up with the network?

    `_node_process` cannot answer this.  It compares `blocks` against
    `headers`, and both come from the node's own view - so a node whose chain
    is blocked reports them equal and reads as healthy.  That is not
    hypothetical: a chainstate that had lost a coin sat six days at height
    961,897 while the network moved on to 962,745, and every `doctor` run in
    that window said "at tip".

    So this asks the two questions the node cannot answer about itself: has it
    marked a branch above its own tip invalid, and where are its peers.
    """
    from mining_dark import bitcoin_node

    name = t("doctor.name.chain")

    if not bitcoin_node.is_running():
        return Check(name, OK, t("doctor.chain.node_stopped"))

    info = bitcoin_node.getblockchaininfo()
    if info is None:
        return Check(name, OK, t("doctor.chain.no_rpc"))

    blocks = int(info.get("blocks", 0))

    # A branch marked invalid above the tip is terminal on its own: it survives
    # restarts, and the node refuses the block from every peer forever.
    branch = bitcoin_node.invalid_branch()
    if branch is not None:
        height, block_hash, length = branch
        return Check(
            name, FAIL,
            t("doctor.chain.invalid", blocks=f"{length:,}",
              height=f"{height:,}", tip=f"{blocks:,}"),
            t("doctor.chain.invalid_fix", hash=block_hash),
        )

    peer_height = bitcoin_node.peer_block_height()
    if peer_height <= 0:
        return Check(name, OK, t("doctor.chain.no_peers"))

    behind = peer_height - blocks
    detail = t("doctor.chain.detail", blocks=f"{blocks:,}",
               peers=f"{peer_height:,}")

    # While the node says it is still downloading, being behind is the job, not
    # a fault - `_node_process` already reports that progress.  What this
    # catches is the node claiming to be caught up while the network is ahead.
    if info.get("initialblockdownload"):
        return Check(name, OK, t("doctor.chain.syncing", detail=detail))

    if behind > MAX_BLOCKS_BEHIND:
        return Check(name, FAIL,
                     t("doctor.chain.stalled", detail=detail,
                       behind=f"{behind:,}"),
                     t("doctor.chain.stalled_fix"))

    return Check(name, OK, t("doctor.chain.at_tip", detail=detail))


def _database(settings) -> Check:
    from mining_dark.utils.utxo_db import UTXODatabase

    name = t("doctor.name.db")
    target = settings.utxo.resolved_db_file()
    if not target.exists():
        return Check(name, WARN, t("doctor.db.absent"), t("doctor.db.absent_fix"))

    with UTXODatabase(target) as db:
        if not db.is_ready:
            return Check(name, FAIL, t("doctor.db.unreadable"),
                         "mining-dark utxo update --force")
        detail = t("doctor.db.detail",
                   count=f"{db.address_count:,}",
                   size=f"{target.stat().st_size / 1e9:,.2f}",
                   days=db.age_days)
        if db.needs_update:
            return Check(name, WARN, detail, "mining-dark utxo update")
        return Check(name, OK, detail)


def _found_wallets(settings) -> Check:
    """
    Whether a wallet found right now could actually be written.

    Everything else here is reproducible - a chainstate resyncs, a database
    rebuilds, a snapshot downloads again.  A found key is not.  So this asks
    the only question that matters about the output directory, and asks it the
    only way that answers it: by writing a file there and removing it again.
    Probing `os.access` instead would still miss a full disk, a read-only mount
    or a directory that exists but refuses writes.
    """
    from mining_dark.utils.file_manager import find_wallet_files

    name = t("doctor.name.wallets")
    target = settings.output.resolved_found_wallets_dir()

    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".mining-dark-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return Check(
            name, FAIL,
            t("doctor.wallets.unwritable", dir=target, error=exc),
            t("doctor.wallets.unwritable_fix"),
        )

    saved = len(find_wallet_files(target))
    detail = t("doctor.wallets.detail", dir=target, saved=f"{saved:,}")

    # The files themselves are written 0600, so their contents are safe either
    # way; a directory anyone can write to is the real problem, since it lets
    # someone else replace a saved wallet with their own.
    if target.stat().st_mode & 0o002:
        return Check(name, WARN,
                     t("doctor.wallets.world_writable", detail=detail),
                     f"chmod 700 {target}")

    return Check(name, OK, detail)


def _disk(settings) -> Check:
    from mining_dark.gui.services import probe_disk

    name = t("doctor.name.disk")
    disk = probe_disk(settings)
    if not disk.total_bytes:
        return Check(name, WARN, t("doctor.disk.unmeasurable"))

    detail = t("doctor.disk.detail",
               free=f"{disk.free_bytes / 1e9:,.1f}",
               need=f"{disk.estimated_rebuild_bytes / 1e9:,.1f}")
    if not disk.sufficient:
        return Check(name, FAIL, detail, t("doctor.disk.fix"))
    return Check(name, OK, detail)


def _next_step(checks: list) -> str:
    """
    The one thing to do now.

    A failure outranks everything - nothing later can work around it - and
    otherwise the first warning in workflow order is what is blocking, because
    the checks are already listed in the order the steps happen.
    """
    for check in checks:
        if check.status == FAIL and check.fix:
            return check.fix
    for check in checks:
        if check.status == WARN and check.fix:
            return check.fix
    return ""


def run(settings=None) -> Report:
    """
    Look at every part of the setup, in the order the workflow uses them.

    Read-only: nothing here starts, stops or writes anything.
    """
    if settings is None:
        from mining_dark.config.settings import load_settings
        settings = load_settings()

    checks = [
        _binaries(),
        _node_config(),
        _snapshot_file(),
        _snapshot_state(),
        _node_process(),
        _chain_progress(),
        _database(settings),
        _disk(settings),
        _found_wallets(settings),
    ]
    return Report(checks=checks, next_step=_next_step(checks))
