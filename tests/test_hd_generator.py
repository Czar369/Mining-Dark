"""
HD mode has to hand on the seed it derived from.

The generator builds a BIP39 mnemonic, derives the children from it, and used
to drop the phrase on the floor - it never reached `WalletKeys`, so it never
reached the file a hit writes.

That is not what makes a hit recoverable: the WIF saved beside it already
spends the address that matched.  It is what makes the hit *complete*.  A hit
is a hit on one child of a seed, and the scan only looks at `child_count`
children per path; the phrase is what restores the whole tree in an ordinary
wallet and reaches the siblings nobody checked.
"""

from __future__ import annotations

import asyncio

from mnemonic import Mnemonic

from mining_dark.generators.hd_generator import HDWalletGenerator

_PATHS = ["m/44'/0'/0'/0/{i}", "m/84'/0'/0'/0/{i}"]


def _batch(child_count: int = 3, paths: list | None = None) -> list:
    generator = HDWalletGenerator(
        queue=asyncio.Queue(),
        derivation_paths=paths if paths is not None else _PATHS,
        child_count=child_count,
    )
    return generator._generate_batch()


def test_every_key_carries_the_phrase_it_came_from() -> None:
    keys = _batch()

    assert keys
    assert all(k.mnemonic for k in keys)


def test_the_phrase_is_a_valid_bip39_mnemonic() -> None:
    """
    A corrupted phrase restores nothing, and the failure would only surface
    the day someone tried to use it.
    """
    keys = _batch(child_count=1)

    assert Mnemonic("english").check(keys[0].mnemonic)


def test_one_seed_backs_the_whole_batch() -> None:
    """All the children in a batch come off a single seed, so all share it."""
    keys = _batch()

    assert len({k.mnemonic for k in keys}) == 1


def test_each_child_records_the_path_that_produced_it() -> None:
    keys = _batch(child_count=3)

    paths = [k.derivation_path for k in keys]
    assert paths == [
        "m/44'/0'/0'/0/0", "m/44'/0'/0'/0/1", "m/44'/0'/0'/0/2",
        "m/84'/0'/0'/0/0", "m/84'/0'/0'/0/1", "m/84'/0'/0'/0/2",
    ]


def test_the_path_is_resolved_not_a_template() -> None:
    """`{i}` on disk would not say which child of the seed matched."""
    assert all("{i}" not in k.derivation_path for k in _batch())


def test_different_batches_use_different_seeds() -> None:
    """Each batch is a fresh guess; reusing a seed would waste the search."""
    first = _batch(child_count=1)[0].mnemonic
    second = _batch(child_count=1)[0].mnemonic

    assert first != second


def test_the_keys_still_derive_from_that_seed() -> None:
    """
    The recorded phrase has to be the one the private key came from.

    Storing *a* valid phrase that does not reproduce the key would be worse
    than storing nothing - it reads as a working backup and is not one.
    """
    from mining_dark.core.address_generator import AddressGenerator
    from mining_dark.generators.hd_generator import (
        _BIP32Node,
        _derive,
        _parse_path,
    )

    key = _batch(child_count=2, paths=["m/84'/0'/0'/0/{i}"])[1]

    master = _BIP32Node.from_seed(Mnemonic.to_seed(key.mnemonic))
    node = _derive(master, _parse_path(key.derivation_path, 0))

    assert AddressGenerator.from_private_key(node.key).private_key_hex == \
        key.private_key_hex


def test_the_phrase_is_treated_as_secret_by_the_log_guard() -> None:
    """It is key material, so nothing may print it to a log or the screen."""
    from mining_dark.utils.logger import contains_secret

    assert contains_secret(_batch(child_count=1)[0].mnemonic)
