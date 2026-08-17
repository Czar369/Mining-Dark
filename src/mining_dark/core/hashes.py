"""
Cryptographic hash primitives used across the project.

RIPEMD-160 is disabled by default in OpenSSL 3.0+ (Ubuntu 22.04+, macOS 13+
with Homebrew OpenSSL 3), which breaks `hashlib.new("ripemd160")` unless the
legacy provider is loaded.  This module provides a hard-guarantee fallback
using pycryptodome so the scanner runs on every modern platform without extra
configuration.
"""

from __future__ import annotations

import hashlib


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


# ----- RIPEMD-160 with pycryptodome fallback ---------------------------------
def _make_ripemd160() -> "callable":
    """Return a callable ripemd160(bytes) -> bytes that always works."""
    try:
        # Fast path: OpenSSL still exposes it (older systems, legacy provider on)
        hashlib.new("ripemd160")

        def _hashlib_ripemd160(data: bytes) -> bytes:
            h = hashlib.new("ripemd160")
            h.update(data)
            return h.digest()

        return _hashlib_ripemd160
    except (ValueError, AttributeError):
        # OpenSSL 3.0 without legacy provider - use pycryptodome
        from Crypto.Hash import RIPEMD160  # type: ignore[import-not-found]

        def _crypto_ripemd160(data: bytes) -> bytes:
            return RIPEMD160.new(data).digest()

        return _crypto_ripemd160


_ripemd160 = _make_ripemd160()


def ripemd160(data: bytes) -> bytes:
    return _ripemd160(data)


def hash160(data: bytes) -> bytes:
    """RIPEMD-160(SHA-256(data)) - standard Bitcoin hash."""
    return ripemd160(sha256(data))
