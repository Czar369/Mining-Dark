"""Wallet data models - pure data, no crypto logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class WalletKeys:
    """All keys and addresses derived from a single private key."""

    private_key_hex: str
    private_key_wif: str
    private_key_wif_uncompressed: str

    public_key_compressed: str
    public_key_uncompressed: str

    # Address formats derived from the compressed key
    p2pkh: str           # Legacy            1...  (compressed)
    p2sh_p2wpkh: str     # Nested SegWit     3...
    p2wpkh: str          # Native SegWit     bc1q... (42)
    p2wsh: str           # SegWit script     bc1q... (62)
    p2tr: str            # Taproot           bc1p...

    # P2PKH off the *uncompressed* key (65 bytes).  Covers Satoshi-era P2PK
    # outputs (blocks 0 to ~170,000), where the UTXO is indexed by the hash of
    # the uncompressed pubkey and so lands on a different address than the
    # compressed p2pkh above.
    p2pkh_uncompressed: str

    # ----- HD provenance, empty in random mode ------------------------------
    # The WIF above already spends this address, so these are not needed to
    # recover the money.  They are kept because an HD hit is a hit on *one
    # child* of a seed: the phrase plus the path is what lets a normal wallet
    # restore the whole tree and sweep the siblings this scan never checked.
    # Discarding them threw away information the generator had in hand.
    mnemonic: str = ""
    derivation_path: str = ""

    @property
    def all_addresses(self) -> dict[str, str]:
        return {
            "p2pkh":              self.p2pkh,
            "p2pkh_uncompressed": self.p2pkh_uncompressed,
            "p2sh_p2wpkh":        self.p2sh_p2wpkh,
            "p2wpkh":             self.p2wpkh,
            "p2wsh":              self.p2wsh,
            "p2tr":               self.p2tr,
        }


@dataclass(slots=True)
class WalletBalance:
    """Balance for a single address from the local UTXO database."""

    address: str
    address_type: str
    confirmed_satoshis: int = 0
    unconfirmed_satoshis: int = 0
    tx_count: int = 0
    source: str = ""

    @property
    def has_balance(self) -> bool:
        return self.confirmed_satoshis > 0 or self.unconfirmed_satoshis > 0

    @property
    def confirmed_btc(self) -> float:
        return self.confirmed_satoshis / 1e8

    @property
    def unconfirmed_btc(self) -> float:
        return self.unconfirmed_satoshis / 1e8

    @property
    def total_satoshis(self) -> int:
        return self.confirmed_satoshis + self.unconfirmed_satoshis


@dataclass(slots=True)
class FoundWallet:
    """A wallet confirmed to have non-zero balance - saved to disk."""

    keys: WalletKeys
    balances: list[WalletBalance]
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def total_confirmed_satoshis(self) -> int:
        return sum(b.confirmed_satoshis for b in self.balances)

    @property
    def total_unconfirmed_satoshis(self) -> int:
        return sum(b.unconfirmed_satoshis for b in self.balances)

    @property
    def primary_address(self) -> str:
        """The first address that has confirmed balance."""
        for b in self.balances:
            if b.confirmed_satoshis > 0:
                return b.address
        return self.balances[0].address if self.balances else ""

    @property
    def primary_address_type(self) -> str:
        for b in self.balances:
            if b.confirmed_satoshis > 0:
                return b.address_type
        return self.balances[0].address_type if self.balances else ""
