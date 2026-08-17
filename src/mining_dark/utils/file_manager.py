"""Persists found wallets to disk as .txt, .json, and rolling CSV summary."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from mining_dark import paths
from mining_dark.core.wallet import FoundWallet

_TXT_TEMPLATE = """\
═══════════════════════════════════════════════════════════════
  BITCOIN WALLET FOUND - Mining-Dark
═══════════════════════════════════════════════════════════════
  Found at   : {found_at}
  Source     : {source}

━━━━━━━━━━━━━━━━━━━━━  PRIVATE KEY  ━━━━━━━━━━━━━━━━━━━━━━━━
  HEX (raw)         : {private_key_hex}
  WIF (compressed)  : {private_key_wif}
  WIF (uncompressed): {private_key_wif_uncompressed}
{recovery_block}
━━━━━━━━━━━━━━━━━━━━━  PUBLIC KEY  ━━━━━━━━━━━━━━━━━━━━━━━━━
  Compressed   : {public_key_compressed}
  Uncompressed : {public_key_uncompressed}

━━━━━━━━━━━━━━━━━━━━━  ADDRESSES  ━━━━━━━━━━━━━━━━━━━━━━━━━━
  P2PKH        (Legacy compr): {p2pkh}
  P2PKH        (Uncompressed): {p2pkh_uncompressed}
  P2SH-P2WPKH  (Nested SW)  : {p2sh_p2wpkh}
  P2WPKH       (Native SW)  : {p2wpkh}
  P2WSH        (Witness SH) : {p2wsh}
  P2TR         (Taproot)    : {p2tr}

━━━━━━━━━━━━━━━━━━━━━  BALANCES  ━━━━━━━━━━━━━━━━━━━━━━━━━━━
{balance_lines}
═══════════════════════════════════════════════════════════════
"""

#: Only written for an HD hit.  The WIF above already spends this address, so
#: this block is not what makes the money recoverable - it is what lets a
#: normal wallet restore the whole tree and sweep the sibling children this
#: scan never looked at.
_RECOVERY_TEMPLATE = """
━━━━━━━━━━━━━━━━━━━━━  HD RECOVERY  ━━━━━━━━━━━━━━━━━━━━━━━━
  Seed phrase  : {mnemonic}
  Derivation   : {derivation_path}
"""

_BALANCE_LINE = (
    "  [{address_type}] {address}\n"
    "    Confirmed   : {confirmed_btc:.8f} BTC  ({confirmed_sat} sat)\n"
    "    Unconfirmed : {unconfirmed_btc:.8f} BTC  ({unconfirmed_sat} sat)\n"
    "    Transactions: {tx_count}\n"
    "    Source      : {source}\n"
)

_CSV_HEADER = [
    "discovered_at",
    "primary_address",
    "address_type",
    "confirmed_sat",
    "unconfirmed_sat",
    "private_key_wif",
    "p2pkh",
    "p2sh_p2wpkh",
    "p2wpkh",
    "p2tr",
]

# Only keep alphanumerics from the address for the filename - bech32 chars are
# already safe, but strip anything else defensively so we never write shell
# metacharacters to disk.
_SAFE_ADDR_RE = re.compile(r"[^A-Za-z0-9]")

# Put on the found queue to ask the persistence task to finish its backlog and
# exit on its own.  Cancelling the task instead discards every wallet still
# queued, and does it without a single log line.
SHUTDOWN: object = object()

# How long the backlog gets before we stop waiting and rescue it by hand.
_DRAIN_TIMEOUT = 30.0

# Marks a wallet the normal save could not write - see _emergency_dump.
_EMERGENCY_PREFIX = "EMERGENCY_"


def find_wallet_files(directory: Path) -> list:
    """
    Every saved wallet in `directory`, newest first.

    The .txt is the authoritative copy - it is what save() returns and the only
    thing an emergency dump produces - so listings key off it rather than the
    .json sidecar.  Listing by .json used to hide any wallet whose .json write
    failed after the .txt had already landed.
    """
    if not directory.is_dir():
        return []

    files = {
        path
        for pattern in ("wallet_*.txt", f"{_EMERGENCY_PREFIX}wallet_*.txt")
        for path in directory.glob(pattern)
    }
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _atomic_write(path: Path, content: str) -> None:
    """
    Write `content` to `path` so that the file is either absent or complete.

    A found wallet is the one artefact this program cannot reproduce, so the
    write goes to a temporary file, reaches the platter via fsync, and only
    then replaces the target.  A crash mid-write leaves the old file intact
    and a stray .tmp behind, never a truncated key.
    """
    tmp = path.with_name(f".{path.name}.tmp")

    # 0600 from the moment the file exists: these carry private keys, and the
    # umask default of 0644 leaves them readable by every account on the box.
    # Opening with the mode beats a later chmod, which would leave a window.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())

    os.replace(tmp, path)

    # Persist the rename itself, otherwise a power cut can still lose the
    # directory entry that points at the freshly written file.
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


class FileManager:
    """Handles async writing of found wallets to disk."""

    def __init__(
        self,
        output_dir: str | Path | None = None,
        save_csv: bool = True,
        json_indent: int = 2,
    ) -> None:
        self._dir: Path = Path(output_dir) if output_dir else paths.FOUND_WALLETS_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._save_csv = save_csv
        self._json_indent = json_indent
        self._csv_path = self._dir / "summary.csv"

    async def save(self, found: FoundWallet) -> Path | None:
        """Write .txt and .json files; append to summary CSV.  Returns txt path."""
        ts = found.discovered_at.strftime("%Y%m%d_%H%M%S")
        safe_addr = _SAFE_ADDR_RE.sub("", found.primary_address)[:20]
        stem = f"wallet_{ts}_{safe_addr}"

        txt_path = self._dir / f"{stem}.txt"
        json_path = self._dir / f"{stem}.json"

        # Rendered before any I/O so the emergency path below always has the
        # full key material to fall back on, whatever failed.
        content = self._render_txt(found)

        try:
            # The directory is created in __init__, but it can be removed or
            # unmounted while a scan runs; recreate it on every save.
            self._dir.mkdir(parents=True, exist_ok=True)

            await asyncio.to_thread(_atomic_write, txt_path, content)
            await asyncio.to_thread(_atomic_write, json_path, self._render_json(found))
            if self._save_csv:
                await asyncio.to_thread(self._append_csv, found)

            logger.info(f"Saved found wallet -> {txt_path.name}")
            return txt_path
        except BaseException as exc:
            # Deliberately BaseException: asyncio.CancelledError does not
            # inherit from Exception, and a save cancelled by Ctrl+C is
            # exactly when a key is most likely to be lost for good.
            self._emergency_dump(content, stem, exc)
            if not isinstance(exc, Exception):
                raise
            return None

    # ----- emergency persistence ---------------------------------------------
    def _fallback_dirs(self) -> list:
        """
        Where to try writing when the configured directory will not take it.

        The working directory is deliberately absent.  It is the project root
        whenever the program is launched from a clone, so a rescued wallet
        landed among the tracked files - outside .gitignore, one `git add -A`
        away from publishing a private key.  Home and the temp directory are
        both writable and outside any repository.
        """
        candidates = [self._dir, Path.home(), Path(tempfile.gettempdir())]

        seen = set()
        unique = []
        for directory in candidates:
            try:
                resolved = directory.resolve()
            except OSError:
                continue
            if resolved not in seen:
                seen.add(resolved)
                unique.append(resolved)
        return unique

    def _emergency_dump(self, content: str, stem: str, exc: BaseException) -> None:
        """
        Last resort after a failed save: get the key onto *something*.

        In random mode the key was never derived from a seed, so if this
        content is lost it cannot be regenerated - the wallet is gone
        permanently.  That justifies falling back through every writable
        location and, if all of them fail, printing the key to stderr.  This
        is the one place in the program that deliberately bypasses the log
        redaction, and it only runs when the wallet is otherwise lost.
        """
        name = f"{_EMERGENCY_PREFIX}{stem}.txt"

        for directory in self._fallback_dirs():
            try:
                path = directory / name
                _atomic_write(path, content)
            except Exception:  # noqa: BLE001 - try every candidate in turn
                continue
            # The path names the address only; the key stays out of the log.
            logger.error(f"Failed to save wallet ({exc}); emergency copy at {path}")
            return

        logger.error(f"Failed to save wallet ({exc}); no writable directory left")
        sys.stderr.write(
            "\n"
            "!!! WALLET COULD NOT BE WRITTEN TO DISK - COPY THE KEY BELOW NOW !!!\n"
            f"{content}\n"
            "!!! END OF WALLET - IT EXISTS NOWHERE ELSE !!!\n"
        )
        sys.stderr.flush()

    # ----- rendering ----------------------------------------------------------
    def _render_txt(self, found: FoundWallet) -> str:
        balance_lines = ""
        sources = set()
        for b in found.balances:
            balance_lines += _BALANCE_LINE.format(
                address_type=b.address_type.upper(),
                address=b.address,
                confirmed_btc=b.confirmed_btc,
                confirmed_sat=b.confirmed_satoshis,
                unconfirmed_btc=b.unconfirmed_btc,
                unconfirmed_sat=b.unconfirmed_satoshis,
                tx_count=b.tx_count,
                source=b.source,
            )
            sources.add(b.source)

        keys = found.keys
        recovery = _RECOVERY_TEMPLATE.format(
            mnemonic=keys.mnemonic,
            derivation_path=keys.derivation_path or "-",
        ) if keys.mnemonic else ""

        return _TXT_TEMPLATE.format(
            found_at=found.discovered_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            source=", ".join(sorted(sources)),
            recovery_block=recovery,
            private_key_hex=found.keys.private_key_hex,
            private_key_wif=found.keys.private_key_wif,
            private_key_wif_uncompressed=found.keys.private_key_wif_uncompressed,
            public_key_compressed=found.keys.public_key_compressed,
            public_key_uncompressed=found.keys.public_key_uncompressed,
            p2pkh=found.keys.p2pkh,
            p2pkh_uncompressed=found.keys.p2pkh_uncompressed,
            p2sh_p2wpkh=found.keys.p2sh_p2wpkh,
            p2wpkh=found.keys.p2wpkh,
            p2wsh=found.keys.p2wsh,
            p2tr=found.keys.p2tr,
            balance_lines=balance_lines.rstrip(),
        )

    def _render_json(self, found: FoundWallet) -> str:
        data = {
            "discovered_at": found.discovered_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "private_key": {
                "hex": found.keys.private_key_hex,
                "wif_compressed": found.keys.private_key_wif,
                "wif_uncompressed": found.keys.private_key_wif_uncompressed,
            },
            # Present only for an HD hit; see _RECOVERY_TEMPLATE.
            "hd": {
                "mnemonic": found.keys.mnemonic,
                "derivation_path": found.keys.derivation_path,
            } if found.keys.mnemonic else None,
            "public_key": {
                "compressed": found.keys.public_key_compressed,
                "uncompressed": found.keys.public_key_uncompressed,
            },
            "addresses": found.keys.all_addresses,
            "balances": [
                {
                    "address": b.address,
                    "address_type": b.address_type,
                    "confirmed_satoshis": b.confirmed_satoshis,
                    "unconfirmed_satoshis": b.unconfirmed_satoshis,
                    "tx_count": b.tx_count,
                    "source": b.source,
                }
                for b in found.balances
            ],
            "total_confirmed_satoshis": found.total_confirmed_satoshis,
            "total_unconfirmed_satoshis": found.total_unconfirmed_satoshis,
        }
        return json.dumps(data, indent=self._json_indent, ensure_ascii=False)

    def _append_csv(self, found: FoundWallet) -> None:
        # Header only when the file doesn't exist yet.
        need_header = not self._csv_path.exists()

        row = [
            found.discovered_at.isoformat(),
            found.primary_address,
            found.primary_address_type,
            str(found.total_confirmed_satoshis),
            str(found.total_unconfirmed_satoshis),
            found.keys.private_key_wif,
            found.keys.p2pkh,
            found.keys.p2sh_p2wpkh,
            found.keys.p2wpkh,
            found.keys.p2tr,
        ]

        # Use csv.writer for proper quoting/escaping instead of manual joins.
        buf = io.StringIO()
        writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        if need_header:
            writer.writerow(_CSV_HEADER)
        writer.writerow(row)

        # Append cannot be made atomic by renaming, so the summary is flushed
        # to disk instead; the .txt above is the authoritative copy anyway.
        #
        # 0600 like the .txt/.json sidecars: this file aggregates the WIF of
        # every found wallet, so it is the most sensitive artefact of the lot
        # and must not inherit the world-readable umask default that the other
        # writes here guard against.  O_CREAT sets the mode when the file is
        # first made; the chmod also tightens a summary.csv left loose on disk
        # by an older build that used a plain open().
        fd = os.open(self._csv_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8", newline="") as handle:
            handle.write(buf.getvalue())
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(self._csv_path, 0o600)


async def shutdown_persistence(
    queue: asyncio.Queue,
    task: asyncio.Task,
    file_manager: FileManager,
    on_saved: Callable[[Path], None] | None = None,
    on_error: Callable[[BaseException], None] | None = None,
) -> None:
    """
    Stop the persistence task without dropping anything it had not written yet.

    Both the CLI's Ctrl+C path and the GUI's STOP button used to cancel this
    task outright, which silently threw away every wallet still on the queue
    plus the one being written at that moment.  Here the task is asked to
    finish first, and whatever it still could not reach is written by hand.
    """
    queue.put_nowait(SHUTDOWN)

    try:
        await asyncio.wait_for(task, timeout=_DRAIN_TIMEOUT)
    except asyncio.TimeoutError:
        logger.error("Persistence task did not drain in time; rescuing the backlog")
    except Exception as exc:  # noqa: BLE001 - the rescue below still has to run
        logger.error(f"Persistence task failed while draining: {exc}")

    if not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    while not queue.empty():
        item = queue.get_nowait()
        queue.task_done()
        if item is SHUTDOWN:
            continue
        try:
            saved = await file_manager.save(item)
        except Exception as exc:  # noqa: BLE001 - one bad wallet must not stop the rest
            if on_error is not None:
                on_error(exc)
            continue
        if saved is not None and on_saved is not None:
            on_saved(saved)
