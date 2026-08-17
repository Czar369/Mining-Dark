"""
HD Wallet key generator - BIP39 / BIP32 / BIP44 / BIP49 / BIP84 / BIP86.

Each batch starts from a fresh BIP39 mnemonic (24 words, 256 bits of entropy),
stretched into a seed by BIP39's PBKDF2-HMAC-SHA512 with an empty passphrase.
That seed becomes the BIP32 master node, and every configured BIP44/49/84/86
path is derived from it - so one mnemonic yields `child_count` keys per path.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import struct
from collections.abc import Callable, Sequence
from dataclasses import replace

import coincurve
from mnemonic import Mnemonic

from mining_dark.core.address_generator import AddressGenerator
from mining_dark.core.wallet import WalletKeys

_SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_HARDENED = 0x80000000

DEFAULT_PATHS = (
    "m/44'/0'/0'/0/{i}",
    "m/49'/0'/0'/0/{i}",
    "m/84'/0'/0'/0/{i}",
    "m/86'/0'/0'/0/{i}",
)

DEFAULT_CHILD_COUNT = 20


def _hmac_sha512(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha512).digest()


class _BIP32Node:
    __slots__ = ("chain_code", "key")

    def __init__(self, key: bytes, chain_code: bytes) -> None:
        self.key = key
        self.chain_code = chain_code

    @classmethod
    def from_seed(cls, seed: bytes) -> _BIP32Node:
        digest = _hmac_sha512(b"Bitcoin seed", seed)
        return cls(digest[:32], digest[32:])

    def child(self, index: int) -> _BIP32Node:
        if index >= _HARDENED:
            data = b"\x00" + self.key + struct.pack(">I", index)
        else:
            pub = coincurve.PrivateKey(self.key).public_key.format(compressed=True)
            data = pub + struct.pack(">I", index)
        digest = _hmac_sha512(self.chain_code, data)
        il, ir = digest[:32], digest[32:]
        il_int = int.from_bytes(il, "big")
        key_int = (il_int + int.from_bytes(self.key, "big")) % _SECP256K1_ORDER
        if il_int >= _SECP256K1_ORDER or key_int == 0:
            # BIP32 says try next index; recursion depth is bounded (probability ~2^-127).
            return self.child(index + 1)
        return _BIP32Node(key_int.to_bytes(32, "big"), ir)


def _parse_path(path: str, child_index: int) -> list[int]:
    path = path.format(i=child_index).strip()
    parts = path.split("/")
    if parts[0] == "m":
        parts = parts[1:]
    indices: list[int] = []
    for p in parts:
        hardened = p.endswith("'")
        n = int(p.rstrip("'"))
        indices.append(n + _HARDENED if hardened else n)
    return indices


def _derive(master: _BIP32Node, indices: list[int]) -> _BIP32Node:
    node = master
    for idx in indices:
        node = node.child(idx)
    return node


class HDWalletGenerator:
    def __init__(
        self,
        queue: asyncio.Queue[WalletKeys],
        derivation_paths: Sequence[str] = DEFAULT_PATHS,
        child_count: int = DEFAULT_CHILD_COUNT,
        stats=None,
        on_key_generated: Callable[[WalletKeys], None] | None = None,
    ) -> None:
        self._queue = queue
        self._paths = list(derivation_paths)
        self._child_count = child_count
        self._stats = stats
        self._on_key_generated = on_key_generated
        self._running = False
        self._mnemo = Mnemonic("english")

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        self._running = True
        loop = asyncio.get_running_loop()

        while self._running:
            wallets = await loop.run_in_executor(None, self._generate_batch)
            for wallet in wallets:
                if not self._running:
                    return
                if self._stats is not None:
                    self._stats.increment(keys_generated=1)
                if self._on_key_generated is not None:
                    self._on_key_generated(wallet)
                await self._queue.put(wallet)

    def _generate_batch(self) -> list[WalletKeys]:
        # `generate` returns the BIP39 *phrase*, not the entropy behind it, and
        # `to_seed` is the BIP39 stretch - the empty passphrase is what makes
        # this recoverable in any ordinary wallet from the phrase alone.
        phrase = self._mnemo.generate(strength=256)
        seed = Mnemonic.to_seed(phrase)
        master = _BIP32Node.from_seed(seed)
        results: list[WalletKeys] = []
        for path_template in self._paths:
            for i in range(self._child_count):
                indices = _parse_path(path_template, i)
                node = _derive(master, indices)
                # `replace`, not assignment: WalletKeys is frozen.  Both
                # values were already in hand here and used to be dropped.
                results.append(replace(
                    AddressGenerator.from_private_key(node.key),
                    mnemonic=phrase,
                    derivation_path=path_template.format(i=i),
                ))
        return results
