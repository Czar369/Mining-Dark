"""
WalletRow reads only the public metadata a found wallet's .json carries, and it
must survive a file it cannot make sense of: one malformed or foreign-schema
sidecar among hundreds must not break the whole found-wallets browser.
"""

from __future__ import annotations

import json
from pathlib import Path

from mining_dark.gui.panels.wallets import WalletRow


def _row(tmp_path: Path, payload) -> WalletRow:
    txt = tmp_path / "wallet_x.txt"
    txt.write_text("body", encoding="utf-8")
    js = tmp_path / "wallet_x.json"
    js.write_text(json.dumps(payload) if not isinstance(payload, str) else payload,
                  encoding="utf-8")
    return WalletRow(txt, js)


def test_a_well_formed_sidecar_is_read(tmp_path: Path) -> None:
    row = _row(tmp_path, {
        "discovered_at": "2026-01-01T12:00:00Z",
        "balances": [{"address": "1AbC", "address_type": "p2pkh"}],
        "total_confirmed_satoshis": 150_000_000,
    })
    assert row.address == "1AbC"
    assert row.address_type == "p2pkh"
    assert row.btc == 1.5


def test_a_valid_json_of_the_wrong_shape_does_not_crash(tmp_path: Path) -> None:
    """A string where a number belongs used to raise past the json.loads guard."""
    row = _row(tmp_path, {"discovered_at": "2026-01-01",
                          "total_confirmed_satoshis": "muito"})
    assert row.btc == 0.0                    # placeholder kept, no exception
    assert row.discovered_at == "2026-01-01"  # what parsed before the bad field


def test_balances_that_are_not_dicts_do_not_crash(tmp_path: Path) -> None:
    row = _row(tmp_path, {"balances": [42, 43]})
    assert row.address == "?"


def test_unparseable_json_leaves_placeholders(tmp_path: Path) -> None:
    row = _row(tmp_path, "{not valid json")
    assert row.address == "?"
    assert row.btc == 0.0


def test_a_missing_sidecar_is_fine(tmp_path: Path) -> None:
    """Emergency dumps write only a .txt; the row must still list the file."""
    txt = tmp_path / "wallet_y.txt"
    txt.write_text("body", encoding="utf-8")
    row = WalletRow(txt, tmp_path / "wallet_y.json")   # json does not exist
    assert row.txt_path == txt
    assert row.address == "?"
