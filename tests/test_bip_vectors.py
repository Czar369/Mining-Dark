"""
Proof that HD mode derives exactly like any BIP-compliant wallet.

The question this answers: if you put a seed into any standard wallet, would it
produce the same addresses this scanner does?  Yes - every compliant wallet
follows the same public standards (BIP32/39/44/49/84/86).  So if this code, fed
the canonical BIP test mnemonic, reproduces the addresses the BIPs themselves
publish, it reproduces what any compliant wallet would produce from that seed.

Vectors are the official ones from the bitcoin/bips repository, cross-checked
against the address each BIP document lists for account 0, first receive slot.
"""

from __future__ import annotations

import pytest

from mining_dark.core.address_generator import AddressGenerator
from mining_dark.generators.hd_generator import _BIP32Node, _derive, _parse_path

# The canonical all-zero BIP-39 mnemonic used across every BIP's test vectors.
_MNEMONIC = "abandon " * 11 + "about"

# (name, path template, WalletKeys attribute, address the BIP publishes)
_VECTORS = [
    ("BIP84 native segwit", "m/84'/0'/0'/0/{i}", "p2wpkh",
     "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"),
    ("BIP49 nested segwit", "m/49'/0'/0'/0/{i}", "p2sh_p2wpkh",
     "37VucYSaXLCAsxYyAPfbSi9eh4iEcbShgf"),
    ("BIP86 taproot", "m/86'/0'/0'/0/{i}", "p2tr",
     "bc1p5cyxnuxmeuwuvkwfem96lqzszd02n6xdcjrs20cac6yqjjwudpxqkedrcr"),
    ("BIP44 legacy", "m/44'/0'/0'/0/{i}", "p2pkh",
     "1LqBGSKuX5yYUonjxT5qGfpUsXKYYWeabA"),
]


@pytest.fixture(scope="module")
def master():
    from mnemonic import Mnemonic

    return _BIP32Node.from_seed(Mnemonic.to_seed(_MNEMONIC))


@pytest.mark.parametrize("name, path, attr, expected", _VECTORS,
                         ids=[v[0] for v in _VECTORS])
def test_matches_the_official_bip_vector(master, name, path, attr, expected) -> None:
    node = _derive(master, _parse_path(path, 0))
    address = getattr(AddressGenerator.from_private_key(node.key), attr)

    assert address == expected
