"""
Balance lookups against the local UTXO SQLite file.

No network calls involved, so a lookup costs a single indexed SELECT.
"""

from __future__ import annotations

from typing import Optional

from mining_dark.core.wallet import WalletBalance
from mining_dark.utils.utxo_db import UTXODatabase


class LocalUTXOChecker:
    """Checks address balances against the local UTXO set."""

    def __init__(self, db: UTXODatabase) -> None:
        self._db = db
        self._ready = False

    @property
    def is_available(self) -> bool:
        """
        Whether the database can answer lookups.

        `UTXODatabase.is_ready` reads address_count, which is a SELECT against
        the meta table.  Asked once per address checked, that single row was
        costing more than the balance lookup it was guarding.  A database that
        has become ready cannot become un-ready while this connection stays
        open, so the answer is cached once it turns True - and still re-checked
        while it is False, in case the connection opens after construction.
        """
        if not self._ready:
            self._ready = self._db.is_ready
        return self._ready

    def check_address(self, address: str, address_type: str) -> Optional[WalletBalance]:
        """
        Look the address up locally.  Always returns a WalletBalance (zeroed
        when the address isn't in the set); None only when the database isn't
        open or is empty.
        """
        if not self.is_available:
            return None

        satoshis = self._db.get_balance(address)
        return WalletBalance(
            address=address,
            address_type=address_type,
            confirmed_satoshis=satoshis,
            unconfirmed_satoshis=0,
            tx_count=1 if satoshis > 0 else 0,
            source="local_utxo",
        )
