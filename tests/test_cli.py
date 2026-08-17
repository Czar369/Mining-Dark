"""
The `check` command must not miss a funded wallet on a case difference.

Bech32 addresses are case-insensitive (BIP173) and stored lowercase, so an
upper- or mixed-case one has to be normalised before the exact-match lookup -
otherwise `mining-dark check <UPPER-bech32>` answers "no balance" for a wallet
that has one.  Base58 is case-sensitive and must survive untouched.
"""

from __future__ import annotations

from mining_dark.cli import _normalize_address

_BECH32 = "bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu"


def test_uppercase_bech32_is_lowered() -> None:
    assert _normalize_address(_BECH32.upper()) == _BECH32


def test_mixed_case_bech32_is_lowered() -> None:
    mixed = "BC1QCR8te4kr609gcawutmrza0j4xv80jy8z306fyu"
    assert _normalize_address(mixed) == _BECH32


def test_lowercase_bech32_is_unchanged() -> None:
    assert _normalize_address(_BECH32) == _BECH32


def test_testnet_bech32_is_lowered() -> None:
    tb = "TB1QW508D6QEJXTDG4Y5R3ZARVARY0C5XW7KXPJZSX"
    assert _normalize_address(tb) == tb.lower()


def test_base58_is_left_exactly_as_typed() -> None:
    """Base58 is case-sensitive: lowering it would corrupt a valid address."""
    for addr in ("1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH",
                 "3QJmV3qfvL9SuYo34YihAf3sRCW3qSinyC"):
        assert _normalize_address(addr) == addr
