"""FileManager writes valid CSV, and never loses a found wallet."""

from __future__ import annotations

import asyncio
import csv
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mining_dark.core.wallet import FoundWallet, WalletBalance, WalletKeys
from mining_dark.utils.file_manager import (
    SHUTDOWN,
    FileManager,
    find_wallet_files,
    shutdown_persistence,
)


def _make_found() -> FoundWallet:
    keys = WalletKeys(
        private_key_hex="a" * 64,
        private_key_wif="KwDiBf89QgGbjEhKnhXJuH7LrciVrZi3qYjgd9M7rFU73sVHnoWn",
        private_key_wif_uncompressed="5HpHagT65TZzG1PH3CSu63k8DbpvD8s5ip4nEB3kEsreAnchuDf",
        public_key_compressed="b" * 66,
        public_key_uncompressed="c" * 130,
        p2pkh="1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH",
        p2sh_p2wpkh="3QJmV3qfvL9SuYo34YihAf3sRCW3qSinyC",
        p2wpkh="bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4",
        p2wsh="bc1qrp33g0q5c5txsp9arysrx4k6zdkfs4nce4xj0gdcccefvpysxf3qccfmv3",
        p2tr="bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr",
        p2pkh_uncompressed="1EHNa6Q4Jz2uvNExL497mE43ikXhwF6kZm",
    )
    balance = WalletBalance(
        address=keys.p2pkh,
        address_type="p2pkh",
        confirmed_satoshis=100_000,
        unconfirmed_satoshis=0,
        tx_count=1,
        source="local_utxo",
    )
    return FoundWallet(
        keys=keys,
        balances=[balance],
        discovered_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_csv_written_with_header_once(tmp_path: Path) -> None:
    fm = FileManager(output_dir=tmp_path, save_csv=True)
    await fm.save(_make_found())
    await fm.save(_make_found())

    csv_path = tmp_path / "summary.csv"
    assert csv_path.exists()

    with open(csv_path, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))

    # 1 header + 2 data rows
    assert len(rows) == 3
    assert rows[0][0] == "discovered_at"
    assert rows[1][2] == "p2pkh"


@pytest.mark.asyncio
async def test_txt_and_json_written(tmp_path: Path) -> None:
    fm = FileManager(output_dir=tmp_path, save_csv=False)
    result = await fm.save(_make_found())

    assert result is not None
    assert result.exists()
    assert result.suffix == ".txt"
    json_path = result.with_suffix(".json")
    assert json_path.exists()


# ═══════════════════════════════════════════════════════════════════════════════
#  A found wallet must never be lost
#
#  In random mode the key came from os.urandom and was never derived from a
#  seed, so anything that does not reach disk is gone permanently.  Every test
#  below asserts on the key material itself, not on the return value.
# ═══════════════════════════════════════════════════════════════════════════════
_PRIVATE_KEY_HEX = "a" * 64


def _key_is_recoverable_under(root: Path) -> bool:
    return any(
        _PRIVATE_KEY_HEX in path.read_text(errors="ignore")
        for path in root.rglob("*")
        if path.is_file()
    )


@pytest.mark.asyncio
async def test_key_survives_an_unwritable_directory(tmp_path: Path) -> None:
    out = tmp_path / "found"
    out.mkdir()
    rescue = tmp_path / "rescue"
    rescue.mkdir()

    fm = FileManager(output_dir=out, save_csv=False)
    fm._fallback_dirs = lambda: [out, rescue]  # type: ignore[method-assign]
    os.chmod(out, 0o500)
    try:
        assert await fm.save(_make_found()) is None
    finally:
        os.chmod(out, 0o700)

    assert _key_is_recoverable_under(rescue)


@pytest.mark.asyncio
async def test_directory_removed_mid_scan_is_recreated(tmp_path: Path) -> None:
    out = tmp_path / "found"
    fm = FileManager(output_dir=out, save_csv=False)
    shutil.rmtree(out)

    result = await fm.save(_make_found())

    assert result is not None and result.exists()


@pytest.mark.asyncio
async def test_cancelled_save_dumps_the_key_and_propagates(tmp_path: Path) -> None:
    """CancelledError is a BaseException, so a bare `except Exception` misses it."""
    fm = FileManager(output_dir=tmp_path, save_csv=False)

    task = asyncio.create_task(fm.save(_make_found()))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert _key_is_recoverable_under(tmp_path)


@pytest.mark.asyncio
async def test_stderr_is_the_last_resort(tmp_path: Path, capsys) -> None:
    """When no directory takes the write, the key still has to reach the operator."""
    out = tmp_path / "found"
    out.mkdir()
    fm = FileManager(output_dir=out, save_csv=False)
    fm._fallback_dirs = lambda: []  # type: ignore[method-assign]
    os.chmod(out, 0o500)
    try:
        assert await fm.save(_make_found()) is None
    finally:
        os.chmod(out, 0o700)

    assert _PRIVATE_KEY_HEX in capsys.readouterr().err


@pytest.mark.asyncio
async def test_saved_files_are_never_partial(tmp_path: Path) -> None:
    """Writes go through a temp file, so a reader sees whole files or none."""
    fm = FileManager(output_dir=tmp_path, save_csv=True)
    result = await fm.save(_make_found())

    assert result is not None
    assert "PRIVATE KEY" in result.read_text()
    assert list(tmp_path.glob(".*tmp")) == []


# ═══════════════════════════════════════════════════════════════════════════════
#  Stopping the scan must not discard the backlog
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_shutdown_writes_every_queued_wallet(tmp_path: Path) -> None:
    """Ctrl+C and the GUI STOP button used to drop whatever was still queued."""
    fm = FileManager(output_dir=tmp_path, save_csv=False)
    queue: asyncio.Queue = asyncio.Queue()

    async def persist() -> None:
        while True:
            item = await queue.get()
            try:
                if item is SHUTDOWN:
                    return
                await fm.save(item)
            finally:
                queue.task_done()

    task = asyncio.create_task(persist())
    for seconds in range(5):
        # Distinct timestamps, otherwise all five share one filename.
        found = _make_found()
        found.discovered_at = found.discovered_at.replace(second=seconds)
        queue.put_nowait(found)

    await shutdown_persistence(queue, task, fm)

    assert len(list(tmp_path.glob("wallet_*.txt"))) == 5
    assert queue.empty()


@pytest.mark.asyncio
async def test_backlog_is_rescued_when_the_task_is_already_dead(tmp_path: Path) -> None:
    """A crashed persistence task must not take the queued wallets with it."""
    fm = FileManager(output_dir=tmp_path, save_csv=False)
    queue: asyncio.Queue = asyncio.Queue()

    async def persist() -> None:
        raise RuntimeError("persistence died")

    task = asyncio.create_task(persist())
    await asyncio.sleep(0)
    for _ in range(3):
        queue.put_nowait(_make_found())

    await shutdown_persistence(queue, task, fm)

    assert len(list(tmp_path.glob("wallet_*.txt"))) == 1  # same stem, overwritten
    assert _key_is_recoverable_under(tmp_path)
    assert queue.empty()


# ═══════════════════════════════════════════════════════════════════════════════
#  Listing found wallets
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_listing_finds_wallets_without_a_json(tmp_path: Path) -> None:
    """Listing by .json hid any wallet whose sidecar write failed."""
    fm = FileManager(output_dir=tmp_path, save_csv=False)
    saved = await fm.save(_make_found())
    saved.with_suffix(".json").unlink()

    assert find_wallet_files(tmp_path) == [saved]


@pytest.mark.asyncio
async def test_listing_includes_emergency_dumps(tmp_path: Path) -> None:
    """A rescued wallet is the one most in need of being seen."""
    fm = FileManager(output_dir=tmp_path, save_csv=False)
    fm._fallback_dirs = lambda: [tmp_path]  # type: ignore[method-assign]
    fm._emergency_dump(
        fm._render_txt(_make_found()), "wallet_20260101_120000_1AAA", RuntimeError("disco cheio")
    )

    listed = find_wallet_files(tmp_path)
    assert len(listed) == 1
    assert listed[0].name.startswith("EMERGENCY_")


def test_listing_a_missing_directory_is_empty(tmp_path: Path) -> None:
    assert find_wallet_files(tmp_path / "nope") == []


# ═══════════════════════════════════════════════════════════════════════════════
#  A rescued wallet must not become a published private key
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_wallet_files_are_readable_only_by_their_owner(tmp_path: Path) -> None:
    """The umask default of 0644 exposes private keys to every account on the box."""
    fm = FileManager(output_dir=tmp_path, save_csv=False)
    saved = await fm.save(_make_found())

    assert saved is not None
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600
    assert stat.S_IMODE(saved.with_suffix(".json").stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_summary_csv_is_readable_only_by_its_owner(tmp_path: Path) -> None:
    """
    summary.csv aggregates the WIF of every found wallet, so it is the single
    most sensitive file - it must get the same 0600 the .txt/.json do, not the
    world-readable umask default.
    """
    fm = FileManager(output_dir=tmp_path, save_csv=True)
    await fm.save(_make_found())

    csv_path = tmp_path / "summary.csv"
    assert csv_path.exists()
    assert stat.S_IMODE(csv_path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_a_loose_summary_csv_is_tightened_on_next_write(tmp_path: Path) -> None:
    """A summary.csv left world-readable by an older build must be fixed, not kept."""
    csv_path = tmp_path / "summary.csv"
    csv_path.write_text("discovered_at\n", encoding="utf-8")
    os.chmod(csv_path, 0o644)

    fm = FileManager(output_dir=tmp_path, save_csv=True)
    await fm.save(_make_found())

    assert stat.S_IMODE(csv_path.stat().st_mode) == 0o600


def test_the_rescue_chain_never_targets_the_working_directory(tmp_path: Path) -> None:
    """
    cwd is the project root when the program is launched from a clone, so a
    rescued wallet landed among the tracked files - one `git add -A` from
    publishing a private key.
    """
    fm = FileManager(output_dir=tmp_path, save_csv=False)

    assert Path.cwd().resolve() not in fm._fallback_dirs()


@pytest.mark.asyncio
async def test_the_emergency_copy_is_also_owner_only(tmp_path: Path) -> None:
    out = tmp_path / "found"
    out.mkdir()
    rescue = tmp_path / "rescue"
    rescue.mkdir()

    fm = FileManager(output_dir=out, save_csv=False)
    fm._fallback_dirs = lambda: [out, rescue]  # type: ignore[method-assign]
    os.chmod(out, 0o500)
    try:
        await fm.save(_make_found())
    finally:
        os.chmod(out, 0o700)

    rescued = list(rescue.glob("EMERGENCY_*"))
    assert len(rescued) == 1
    assert stat.S_IMODE(rescued[0].stat().st_mode) == 0o600


# ═══════════════════════════════════════════════════════════════════════════════
#  An HD hit has to carry its seed phrase to disk
# ═══════════════════════════════════════════════════════════════════════════════
#  The generator built a mnemonic, derived the children from it, and then threw
#  the phrase away - it never reached WalletKeys, so it never reached the file.
#  Not a loss of money: the WIF beside it already spends the address that
#  matched.  A loss of reach: a hit is a hit on one *child* of a seed, and the
#  scan only ever looks at `child_count` of them.  The phrase is what restores
#  the whole tree in a normal wallet and sweeps the siblings nobody checked.

_MNEMONIC = ("legal winner thank year wave sausage worth useful legal winner "
             "thank yellow")


def _make_hd_found() -> FoundWallet:
    from dataclasses import replace

    found = _make_found()
    return replace(found, keys=replace(found.keys, mnemonic=_MNEMONIC,
                                       derivation_path="m/84'/0'/0'/0/3"))


def test_an_hd_hit_writes_its_seed_phrase(tmp_path: Path) -> None:
    manager = FileManager(output_dir=tmp_path, save_csv=False)

    path = asyncio.run(manager.save(_make_hd_found()))

    text = path.read_text()
    assert _MNEMONIC in text, "the phrase must be whole - a partial one restores nothing"
    assert "m/84'/0'/0'/0/3" in text


def test_the_derivation_path_is_resolved_not_a_template(tmp_path: Path) -> None:
    """`m/84'/0'/0'/0/{i}` would not say which child matched."""
    manager = FileManager(output_dir=tmp_path, save_csv=False)

    text = asyncio.run(manager.save(_make_hd_found())).read_text()

    assert "{i}" not in text


def test_a_random_hit_gets_no_recovery_block(tmp_path: Path) -> None:
    """There is no seed behind a random key; empty fields would only mislead."""
    manager = FileManager(output_dir=tmp_path, save_csv=False)

    text = asyncio.run(manager.save(_make_found())).read_text()

    assert "HD RECOVERY" not in text
    assert "Seed phrase" not in text


def test_the_json_carries_the_phrase_too(tmp_path: Path) -> None:
    import json

    manager = FileManager(output_dir=tmp_path, save_csv=False)

    path = asyncio.run(manager.save(_make_hd_found()))
    data = json.loads(path.with_suffix(".json").read_text())

    assert data["hd"]["mnemonic"] == _MNEMONIC
    assert data["hd"]["derivation_path"] == "m/84'/0'/0'/0/3"


def test_the_json_says_null_for_a_random_hit(tmp_path: Path) -> None:
    import json

    manager = FileManager(output_dir=tmp_path, save_csv=False)

    path = asyncio.run(manager.save(_make_found()))

    assert json.loads(path.with_suffix(".json").read_text())["hd"] is None


def test_the_private_key_is_written_in_both_modes(tmp_path: Path) -> None:
    """
    The phrase is a bonus; the WIF is what actually spends the coins.

    Whatever else changes about the format, losing this loses the money.
    """
    manager = FileManager(output_dir=tmp_path, save_csv=False)

    for found in (_make_found(), _make_hd_found()):
        text = asyncio.run(manager.save(found)).read_text()
        assert found.keys.private_key_wif in text
        assert found.keys.private_key_wif_uncompressed in text
        assert found.keys.private_key_hex in text


# ═══════════════════════════════════════════════════════════════════════════════
#  What is written must actually recover the wallet
#
#  Presence is not enough - the point of a hit is that the file *spends* it.  This
#  reconstructs the key from the saved material two independent ways and checks
#  both reproduce the exact address (and private key) that was found.
# ═══════════════════════════════════════════════════════════════════════════════
def _make_real_hd_found() -> FoundWallet:
    """
    A genuine HD hit: the key is derived from the seed at the stored path, the
    way the HD generator makes one - so re-deriving from the phrase must land on
    the same key.  (_make_hd_found() just staples a phrase onto a random key.)
    """
    from dataclasses import replace

    from mnemonic import Mnemonic

    from mining_dark.core.address_generator import AddressGenerator
    from mining_dark.generators.hd_generator import _BIP32Node, _derive, _parse_path

    path = "m/84'/0'/0'/0/7"
    seed = Mnemonic.to_seed(_MNEMONIC)
    node = _derive(_BIP32Node.from_seed(seed), _parse_path(path, 7))

    keys = replace(AddressGenerator.from_private_key(node.key),
                   mnemonic=_MNEMONIC, derivation_path=path)
    balance = WalletBalance(address=keys.p2wpkh, address_type="p2wpkh",
                            confirmed_satoshis=1)
    return FoundWallet(keys=keys, balances=[balance])


def test_the_saved_wif_recovers_the_exact_key(tmp_path: Path) -> None:
    """Decoding the stored WIF must give back the key that owns the address."""
    import json

    from mining_dark.core.address_generator import AddressGenerator
    from mining_dark.core.key_generator import KeyGenerator

    # A consistent wallet - hex, WIF and address all from one real key - rather
    # than the hand-built _make_found() fixture, whose fields are fixed strings
    # that do not correspond to each other.
    keys = AddressGenerator.from_private_key(KeyGenerator.generate_private_key())
    found = FoundWallet(keys=keys, balances=[
        WalletBalance(address=keys.p2wpkh, address_type="p2wpkh", confirmed_satoshis=1)
    ])

    saved = asyncio.run(FileManager(output_dir=tmp_path, save_csv=False).save(found))
    stored = json.loads(saved.with_suffix(".json").read_text())["private_key"]

    priv, compressed = KeyGenerator.wif_to_private_key(stored["wif_compressed"])
    assert compressed is True
    assert priv.hex() == found.keys.private_key_hex
    # And the key really controls the address that was reported as funded.
    assert AddressGenerator.from_private_key(priv).p2wpkh == found.keys.p2wpkh


def test_the_saved_seed_and_path_re_derive_the_exact_key(tmp_path: Path) -> None:
    """The phrase + path on disk must reconstruct the very key that was found."""
    import json

    from mnemonic import Mnemonic

    from mining_dark.core.address_generator import AddressGenerator
    from mining_dark.generators.hd_generator import _BIP32Node, _derive, _parse_path

    found = _make_real_hd_found()
    saved = asyncio.run(FileManager(output_dir=tmp_path, save_csv=False).save(found))
    hd = json.loads(saved.with_suffix(".json").read_text())["hd"]

    seed = Mnemonic.to_seed(hd["mnemonic"])
    # The stored path is already resolved (no {i}), so the child index is ignored.
    node = _derive(_BIP32Node.from_seed(seed), _parse_path(hd["derivation_path"], 0))

    assert node.key.hex() == found.keys.private_key_hex
    assert AddressGenerator.from_private_key(node.key).p2wpkh == found.keys.p2wpkh
