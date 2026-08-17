"""Async random private-key generator - fills an asyncio.Queue for workers."""

from __future__ import annotations

import asyncio
from typing import Callable, Optional

from mining_dark.core.address_generator import AddressGenerator
from mining_dark.core.key_generator import KeyGenerator
from mining_dark.core.wallet import WalletKeys


class RandomKeyGenerator:
    """Emits WalletKeys built from random private keys, as fast as it can."""

    def __init__(
        self,
        queue: "asyncio.Queue[WalletKeys]",
        stats=None,
        on_key_generated: Optional[Callable[[WalletKeys], None]] = None,
    ) -> None:
        self._queue = queue
        self._stats = stats
        self._on_key_generated = on_key_generated
        self._running = False

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        """Generate keys until stop() is called."""
        self._running = True
        loop = asyncio.get_running_loop()

        while self._running:
            wallet = await loop.run_in_executor(None, self._generate_one)

            if self._stats is not None:
                self._stats.increment(keys_generated=1)

            if self._on_key_generated is not None:
                self._on_key_generated(wallet)

            await self._queue.put(wallet)

    @staticmethod
    def _generate_one() -> WalletKeys:
        private_key = KeyGenerator.generate_private_key()
        return AddressGenerator.from_private_key(private_key)
