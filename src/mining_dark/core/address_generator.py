"""
Bitcoin address derivation for all standard output types.

P2PKH        -> 1...       (BIP44 legacy)
P2SH-P2WPKH  -> 3...       (BIP49 nested SegWit)
P2WPKH       -> bc1q...42  (BIP84 native SegWit)
P2WSH        -> bc1q...62  (pay-to-witness-script-hash)
P2TR         -> bc1p...    (BIP86 Taproot, BIP340/341)

No external bech32 library used for encoding so we control all edge cases.
"""

from __future__ import annotations

from typing import Optional

import base58
import coincurve

from mining_dark.core.hashes import hash160, sha256, sha256d
from mining_dark.core.key_generator import KeyGenerator
from mining_dark.core.wallet import WalletKeys

# ----- Bech32 / Bech32m (inline reference impl, BIP173 + BIP350) -------------
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_BECH32M_CONST = 0x2BC830A3
_BECH32_CONST = 1


def _b58check(version: bytes, payload: bytes) -> str:
    raw = version + payload
    checksum = sha256d(raw)[:4]
    return base58.b58encode(raw + checksum).decode("ascii")


def _bech32_polymod(values: list[int]) -> int:
    GEN = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= GEN[i] if ((b >> i) & 1) else 0
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data: bytes, frombits: int, tobits: int, pad: bool = True) -> Optional[list[int]]:
    acc, bits, ret = 0, 0, []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            return None
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


def _bech32_encode(hrp: str, witver: int, witprog: bytes) -> str:
    """Encode a SegWit address.  witver=0 -> bech32, witver>=1 -> bech32m."""
    spec_const = _BECH32M_CONST if witver > 0 else _BECH32_CONST
    data = [witver] + (_convertbits(witprog, 8, 5) or [])
    hrp_expanded = _bech32_hrp_expand(hrp)
    polymod = _bech32_polymod(hrp_expanded + data + [0] * 6) ^ spec_const
    checksum = [(polymod >> (5 * (5 - i))) & 31 for i in range(6)]
    return hrp + "1" + "".join(_BECH32_CHARSET[d] for d in data + checksum)


# ----- BIP341 tagged hash (for Taproot key-path spending) --------------------
def _tagged_hash(tag: str, data: bytes) -> bytes:
    tag_hash = sha256(tag.encode("utf-8"))
    return sha256(tag_hash + tag_hash + data)


# ----- Address generators ----------------------------------------------------
def _p2pkh(pubkey: bytes) -> str:
    """Pay-to-Public-Key-Hash - Legacy address (1...)."""
    return _b58check(b"\x00", hash160(pubkey))


def _p2sh_p2wpkh(pubkey: bytes) -> str:
    """Pay-to-Script-Hash wrapping P2WPKH - Nested SegWit (3...)."""
    # Redeem script: OP_0 <20-byte-pubkey-hash>
    redeem_script = b"\x00\x14" + hash160(pubkey)
    return _b58check(b"\x05", hash160(redeem_script))


def _p2wpkh(pubkey: bytes) -> str:
    """Pay-to-Witness-Public-Key-Hash - Native SegWit (bc1q... 42 chars)."""
    return _bech32_encode("bc", 0, hash160(pubkey))


def _p2wsh(pubkey: bytes) -> str:
    """Pay-to-Witness-Script-Hash (bc1q... 62 chars).
    Uses a 1-of-1 multisig script as the witness script for demonstration."""
    # witness script: OP_1 <33-byte-pubkey> OP_1 OP_CHECKMULTISIG
    witness_script = b"\x51" + bytes([len(pubkey)]) + pubkey + b"\x51\xae"
    script_hash = sha256(witness_script)
    return _bech32_encode("bc", 0, script_hash)


def _p2tr(pubkey: bytes) -> str:
    """Pay-to-Taproot - Taproot (bc1p...) using BIP341 key-path spend."""
    # 1. Ensure we have compressed public key (33 bytes)
    if len(pubkey) == 65:  # uncompressed
        pub_obj = coincurve.PublicKey(pubkey)
        pubkey = pub_obj.format(compressed=True)

    # 2. x-only internal key (32 bytes) - lift_x uses even-y version
    x_only = pubkey[1:]  # strip 0x02/0x03 prefix
    internal_key = coincurve.PublicKey(b"\x02" + x_only)  # force even y

    # 3. Taproot tweak: t = H_TapTweak(x_only)  [key-path, no script tree]
    t = _tagged_hash("TapTweak", x_only)

    # 4. Output key Q = P + t·G
    tweaked = internal_key.add(t)
    tweaked_bytes = tweaked.format(compressed=True)
    output_x = tweaked_bytes[1:]  # 32-byte x-coordinate

    return _bech32_encode("bc", 1, output_x)


# ----- Public API ------------------------------------------------------------
class AddressGenerator:
    """Derives all Bitcoin address types from a private key."""

    @staticmethod
    def from_private_key(private_key: bytes) -> WalletKeys:
        """Generate a WalletKeys instance containing every address format."""
        pub_compressed = KeyGenerator.get_public_key(private_key, compressed=True)
        pub_uncompressed = KeyGenerator.get_public_key(private_key, compressed=False)

        return WalletKeys(
            private_key_hex=private_key.hex(),
            private_key_wif=KeyGenerator.get_wif(private_key, compressed=True),
            private_key_wif_uncompressed=KeyGenerator.get_wif(private_key, compressed=False),
            public_key_compressed=pub_compressed.hex(),
            public_key_uncompressed=pub_uncompressed.hex(),
            p2pkh=_p2pkh(pub_compressed),
            p2pkh_uncompressed=_p2pkh(pub_uncompressed),
            p2sh_p2wpkh=_p2sh_p2wpkh(pub_compressed),
            p2wpkh=_p2wpkh(pub_compressed),
            p2wsh=_p2wsh(pub_compressed),
            p2tr=_p2tr(pub_compressed),
        )

    @staticmethod
    def addresses_for_types(wallet: WalletKeys, types: list[str]) -> dict[str, str]:
        """Return only the requested address types from a WalletKeys instance."""
        return {t: addr for t, addr in wallet.all_addresses.items() if t in types}
