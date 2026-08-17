"""
Address derivation regression tests.

The chosen private key (0x01) has well-known deterministic outputs from every
BIP so these values are pinned across all Bitcoin ecosystem tools.
"""

from __future__ import annotations

from mining_dark.core.address_generator import AddressGenerator
from mining_dark.core.hashes import hash160, ripemd160, sha256


PRIV_ONE = bytes.fromhex("0000000000000000000000000000000000000000000000000000000000000001")


def test_ripemd160_matches_known_vector() -> None:
    # RIPEMD-160("") == 9c1185a5c5e9fc54612808977ee8f548b2258d31
    assert ripemd160(b"").hex() == "9c1185a5c5e9fc54612808977ee8f548b2258d31"


def test_hash160_matches_known_vector() -> None:
    # hash160("") == RIPEMD160(SHA256("")) == b472a266d0bd89c13706a4132ccfb16f7c3b9fcb
    assert hash160(b"").hex() == "b472a266d0bd89c13706a4132ccfb16f7c3b9fcb"


def test_addresses_for_priv_one() -> None:
    wallet = AddressGenerator.from_private_key(PRIV_ONE)

    # Well-known Bitcoin addresses for private key = 1
    assert wallet.p2pkh == "1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH"
    assert wallet.p2pkh_uncompressed == "1EHNa6Q4Jz2uvNExL497mE43ikXhwF6kZm"

    # WIF encodings for private key = 1
    assert wallet.private_key_wif == "KwDiBf89QgGbjEhKnhXJuH7LrciVrZi3qYjgd9M7rFU73sVHnoWn"
    assert wallet.private_key_wif_uncompressed == (
        "5HpHagT65TZzG1PH3CSu63k8DbpvD8s5ip4nEB3kEsreAnchuDf"
    )

    # Exact values, not just prefixes.  A prefix-and-length check passes for a
    # taproot address derived without the BIP341 tweak - the single worst bug
    # this project could ship, since every derived address would be wrong while
    # looking perfectly well-formed.
    assert wallet.p2sh_p2wpkh == "3JvL6Ymt8MVWiCNHC7oWU6nLeHNJKLZGLN"
    assert wallet.p2wpkh == "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
    assert wallet.p2tr == (
        "bc1pmfr3p9j00pfxjh0zmgp99y8zftmd3s5pmedqhyptwy6lm87hf5sspknck9"
    )
    # The witness script here is the project's own choice, so this pins the
    # encoding rather than an ecosystem-wide constant.
    assert wallet.p2wsh == (
        "bc1q9qs9xv7mjghkd69fgx62xttxmeww5q7eekjxu0nxtzf4yu4ekf8s4plngs"
    )


def test_taproot_applies_the_tweak() -> None:
    """
    The untweaked key is a well-formed bech32m address that differs only in its
    payload, so nothing but the exact value catches a missing tweak.
    """
    wallet = AddressGenerator.from_private_key(PRIV_ONE)

    untweaked = "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0"
    assert wallet.p2tr.startswith("bc1p"), "sanity: ambos passariam por prefixo"
    assert untweaked.startswith("bc1p")
    assert wallet.p2tr != untweaked


def test_segwit_v1_uses_bech32m_not_bech32() -> None:
    """
    BIP350 changed the checksum constant for witness v1.  Encoding taproot with
    bech32 produces an address of the right shape that no wallet can pay to.
    """
    wallet = AddressGenerator.from_private_key(PRIV_ONE)

    # Same payload under the bech32 (v0) constant - differs only in the checksum.
    assert not wallet.p2tr.endswith("pknck8")
    assert wallet.p2tr.endswith("pknck9")


def test_all_addresses_contains_every_type() -> None:
    wallet = AddressGenerator.from_private_key(PRIV_ONE)
    assert set(wallet.all_addresses.keys()) == {
        "p2pkh",
        "p2pkh_uncompressed",
        "p2sh_p2wpkh",
        "p2wpkh",
        "p2wsh",
        "p2tr",
    }
