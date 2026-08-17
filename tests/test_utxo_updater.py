"""The export command decides what ever reaches the scanner's database."""

from __future__ import annotations

from pathlib import Path

import pytest

from mining_dark import bitcoin_node, paths, utxo_updater


class _Result:
    returncode = 0
    stdout = ""
    stderr = ""


def _capture_cmd(monkeypatch, tmp_path) -> list[str]:
    """Run _run_utxo_dump with subprocess stubbed out and return the argv it built."""
    monkeypatch.setattr(
        bitcoin_node, "require_dumpable_chainstate", lambda: tmp_path / "chainstate"
    )

    seen: list[str] = []

    def _fake_run(cmd, **kwargs):
        seen.extend(cmd)
        # A real dump writes the CSV; _run_utxo_dump now insists on seeing it.
        Path(cmd[cmd.index("-o") + 1]).write_text("address,amount\n1AAA,100\n")
        return _Result()

    monkeypatch.setattr(utxo_updater.subprocess, "run", _fake_run)
    utxo_updater._run_utxo_dump(tmp_path / "out.csv")
    return seen


def test_dump_requests_p2pk_addresses(tmp_path, monkeypatch) -> None:
    """
    Without -p2pkaddresses, P2PK outputs are dumped with an empty address field and
    _parse_csv drops them - so the Satoshi-era coins never land in the database,
    even though every scanned key derives a p2pkh_uncompressed address to match
    them.  Nothing errors; the scanner is simply blind to that whole class.
    """
    assert "-p2pkaddresses" in _capture_cmd(monkeypatch, tmp_path)


def test_dump_exports_the_fields_the_importer_reads(tmp_path, monkeypatch) -> None:
    """_parse_csv reads the `address` and `amount` columns by name."""
    cmd = _capture_cmd(monkeypatch, tmp_path)
    fields = cmd[cmd.index("-f") + 1].split(",")
    assert set(fields) >= {"address", "amount"}


def test_dump_refuses_an_incoherent_chainstate(tmp_path, monkeypatch) -> None:
    """
    The guard belongs on the path that reads the UTXO set: an aborted snapshot load
    would otherwise be dumped as if it were the tip.
    """
    def _boom() -> Path:
        raise bitcoin_node.BitcoinNodeError("chainstate incoerente")

    monkeypatch.setattr(bitcoin_node, "require_dumpable_chainstate", _boom)
    monkeypatch.setattr(
        utxo_updater.subprocess, "run",
        lambda *a, **k: pytest.fail("exportou apesar do chainstate inválido"),
    )

    with pytest.raises(bitcoin_node.BitcoinNodeError):
        utxo_updater._run_utxo_dump(tmp_path / "out.csv")


def test_parse_csv_drops_rows_without_an_address(tmp_path) -> None:
    """
    The behaviour that makes the missing flag silent rather than loud - worth
    pinning, so the reason -p2pkaddresses matters stays visible.
    """
    csv = tmp_path / "dump.csv"
    csv.write_text(
        "address,amount\n"
        "1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH,1000\n"
        ",5000\n"                                    # P2PK row without the flag
        "1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH,250\n",  # same address again
        encoding="utf-8",
    )

    from mining_dark.utils.utxo_db import UTXODatabase

    db = UTXODatabase.create(tmp_path / "utxo.db")

    class _NullProgress:
        def update(self, *a, **k) -> None: ...

    total = utxo_updater._parse_csv(csv, db, _NullProgress(), task_id=0)
    db.commit()

    assert total == 2, "a linha sem endereço deveria ser descartada"
    # Repeated addresses must accumulate, not overwrite.
    assert db.get_balance("1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH") == 1250


# ═══════════════════════════════════════════════════════════════════════════════
#  A rebuild must never destroy a working database
#
#  finalize() replaces the live file, and rebuilding it costs hours.  Since
#  bitcoin-utxo-dump exits 0 even when it exported nothing, the row count is
#  the only thing standing between a failed export and an empty scanner.
# ═══════════════════════════════════════════════════════════════════════════════
def test_empty_import_is_refused() -> None:
    with pytest.raises(RuntimeError, match="nenhum endereço"):
        utxo_updater._reject_implausible_import(0, previous_count=1_000)


def test_drastic_shrink_is_refused() -> None:
    with pytest.raises(RuntimeError, match="encolhe"):
        utxo_updater._reject_implausible_import(10, previous_count=1_000)


def test_normal_growth_is_accepted() -> None:
    utxo_updater._reject_implausible_import(1_100, previous_count=1_000)
    # First build ever: nothing to compare against.
    utxo_updater._reject_implausible_import(1, previous_count=0)


def test_dump_without_a_csv_is_an_error(tmp_path, monkeypatch) -> None:
    """bitcoin-utxo-dump exits 0 when it cannot read -db, writing no file."""
    monkeypatch.setattr(
        bitcoin_node, "require_dumpable_chainstate", lambda: tmp_path / "chainstate"
    )
    monkeypatch.setattr(utxo_updater.subprocess, "run", lambda *a, **k: _Result())

    with pytest.raises(RuntimeError, match="sem gerar o CSV"):
        utxo_updater._run_utxo_dump(tmp_path / "out.csv")


# ═══════════════════════════════════════════════════════════════════════════════
#  A malformed CSV must not take the whole import down
# ═══════════════════════════════════════════════════════════════════════════════
def _parse(tmp_path, text: str):
    from mining_dark.utils.utxo_db import UTXODatabase

    csv_path = tmp_path / "dump.csv"
    csv_path.write_text(text, encoding="utf-8")
    db = UTXODatabase.create(tmp_path / "utxo.db")

    class _NullProgress:
        def update(self, *a, **k) -> None: ...

    total = utxo_updater._parse_csv(csv_path, db, _NullProgress(), task_id=0)
    db.commit()
    return total, db


def test_truncated_final_line_is_skipped(tmp_path) -> None:
    """
    csv.DictReader fills a missing field with None, not with the default given
    to .get() - int(None) used to raise TypeError and abort the whole import.
    """
    total, db = _parse(tmp_path, "address,amount\n1AAA,100\n1BBB\n")

    assert total == 1
    assert db.get_balance("1AAA") == 100


def test_amounts_above_the_total_supply_are_skipped(tmp_path) -> None:
    """Beyond 21M BTC the value is corruption, and it overflows the upsert."""
    total, db = _parse(
        tmp_path,
        f"address,amount\n1AAA,100\n1BBB,{2**63}\n",
    )

    assert total == 1
    assert db.get_balance("1BBB") == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  The node has to be at the tip, not merely close to it
# ═══════════════════════════════════════════════════════════════════════════════
def test_sync_gate_accepts_a_node_at_the_tip() -> None:
    assert utxo_updater._sync_shortfall({
        "blocks": 961_593,
        "headers": 961_593,
        "verificationprogress": 0.9999999,
        "initialblockdownload": False,
    }) == ""


def test_a_node_behind_the_tip_is_refused_despite_full_progress() -> None:
    """
    verificationprogress is time-weighted and asymptotes to 1, so a node hours
    behind still reports 0.9999+.  Dumping there silently misses recent coins.
    """
    reason = utxo_updater._sync_shortfall({
        "blocks": 960_000,
        "headers": 961_593,
        "verificationprogress": 0.99999,
        "initialblockdownload": False,
    })

    assert "1,593" in reason


def test_initial_block_download_is_refused() -> None:
    reason = utxo_updater._sync_shortfall({
        "blocks": 961_593,
        "headers": 961_593,
        "verificationprogress": 1.0,
        "initialblockdownload": True,
    })

    assert reason


# ═══════════════════════════════════════════════════════════════════════════════
#  Offline export from a validated assumeutxo snapshot
#
#  The RPC sync checks exist to prove the chainstate on disk is complete at the
#  tip.  A loaded snapshot proves that another way - Core writes base_blockhash
#  only after matching the whole set against its hardcoded hash - so this path
#  skips them.  It is the way out when the node cannot start at all.
# ═══════════════════════════════════════════════════════════════════════════════
def test_snapshot_export_refused_without_a_loaded_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "orphaned")

    with pytest.raises(SystemExit):
        utxo_updater._announce_snapshot_export()


def test_snapshot_export_refused_while_the_node_runs(monkeypatch) -> None:
    """A live node can leave the on-disk chainstate behind the tip."""
    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "loaded")
    monkeypatch.setattr(bitcoin_node, "is_running", lambda: True)

    with pytest.raises(SystemExit):
        utxo_updater._announce_snapshot_export()


def test_snapshot_export_allowed_when_loaded_and_stopped(monkeypatch) -> None:
    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "loaded")
    monkeypatch.setattr(bitcoin_node, "is_running", lambda: False)
    monkeypatch.setattr(bitcoin_node, "last_startup_error", lambda **k: "")
    monkeypatch.setattr(bitcoin_node, "snapshot_height_from_log", lambda **k: 961_599)

    # The height is recorded from the log, since offline there is no RPC to ask.
    assert utxo_updater._announce_snapshot_export() == 961_599


def test_snapshot_export_refused_when_the_node_complained(monkeypatch) -> None:
    """
    base_blockhash proves the snapshot was whole when loaded, not that it still
    is - Core keeps applying blocks to that same LevelDB afterwards.  The node's
    own verdict lives in debug.log, and this path never consulted it.
    """
    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "loaded")
    monkeypatch.setattr(bitcoin_node, "is_running", lambda: False)
    monkeypatch.setattr(
        bitcoin_node, "last_startup_error", lambda **k: "Corrupted block database detected."
    )

    with pytest.raises(SystemExit):
        utxo_updater._announce_snapshot_export()


def test_the_complaint_can_be_overridden_explicitly(monkeypatch) -> None:
    """A refusal with no way through would be a dead end after a verified check."""
    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "loaded")
    monkeypatch.setattr(bitcoin_node, "is_running", lambda: False)
    monkeypatch.setattr(
        bitcoin_node, "last_startup_error", lambda **k: "Corrupted block database detected."
    )

    monkeypatch.setattr(bitcoin_node, "snapshot_height_from_log", lambda **k: 0)
    assert utxo_updater._announce_snapshot_export(ignore_node_errors=True) == 0


def test_the_hint_appears_only_when_the_snapshot_can_be_used(monkeypatch) -> None:
    """A node that is down for any other reason must not be told to use it."""
    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "loaded")
    assert "--from-snapshot" in utxo_updater._snapshot_hint()

    for state in ("none", "orphaned", "loading"):
        monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda s=state: s)
        assert utxo_updater._snapshot_hint() == ""


def test_snapshot_export_does_not_touch_the_node(monkeypatch, tmp_path) -> None:
    """The whole point is that it works with bitcoind unable to start."""
    # Into tmp_path, never the real data/utxo: update_from_node takes the
    # rebuild lock on the resolved database, and a test has no business
    # competing with a live scan for it.
    monkeypatch.setattr(utxo_updater, "_resolved_db_file", lambda: tmp_path / "utxo.db")
    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "loaded")
    monkeypatch.setattr(bitcoin_node, "is_running", lambda: False)
    monkeypatch.setattr(bitcoin_node, "last_startup_error", lambda **k: "")
    monkeypatch.setattr(
        bitcoin_node, "require_dumpable_chainstate", lambda: tmp_path / "chainstate"
    )
    monkeypatch.setattr(
        bitcoin_node, "stop", lambda *a, **k: pytest.fail("parou um nó que estava parado")
    )
    monkeypatch.setattr(
        bitcoin_node, "start", lambda *a, **k: pytest.fail("iniciou o nó por conta própria")
    )
    monkeypatch.setattr(
        utxo_updater, "check_bitcoin_core", lambda: pytest.fail("consultou o RPC")
    )

    captured: dict = {}

    def _fake_dump_and_import(source_label, blocks, target_db, *, node_was_running):
        captured.update(source_label=source_label, node_was_running=node_was_running)

    monkeypatch.setattr(utxo_updater, "_dump_and_import", _fake_dump_and_import)

    from mining_dark.utils.utxo_db import UTXODatabase

    monkeypatch.setattr(UTXODatabase, "open", lambda self: False)

    utxo_updater.update_from_node(force=True, from_snapshot=True)

    assert captured == {
        "source_label": "bitcoin_core_assumeutxo",
        "node_was_running": False,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  One rebuild at a time, refused before any work is spent
# ═══════════════════════════════════════════════════════════════════════════════
def test_a_second_update_is_refused_before_stopping_the_node(monkeypatch, tmp_path) -> None:
    """
    Held only around the import, the lock let a second update stop the node and
    spend the whole export writing the same CSV path as the first, before being
    refused at the end - two exports interleaved in one file, and an import that
    would have doubled every balance the overlap touched.
    """
    from mining_dark.utils import db_lock

    db = tmp_path / "utxo.db"
    monkeypatch.setattr(utxo_updater, "_resolved_db_file", lambda: db)
    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: "loaded")
    monkeypatch.setattr(bitcoin_node, "is_running", lambda: False)
    monkeypatch.setattr(
        bitcoin_node, "require_dumpable_chainstate", lambda: tmp_path / "chainstate"
    )
    monkeypatch.setattr(
        bitcoin_node, "stop", lambda *a, **k: pytest.fail("parou o nó antes do lock")
    )
    monkeypatch.setattr(
        utxo_updater, "_run_utxo_dump", lambda *a: pytest.fail("exportou antes do lock")
    )
    monkeypatch.setattr(utxo_updater.console, "print", lambda *a, **k: None)

    # Another rebuild already owns the database.
    with db_lock.rebuilding(db), pytest.raises(db_lock.DatabaseBusyError):
        utxo_updater.update_from_node(force=True, from_snapshot=True)


def test_a_refused_import_keeps_the_csv(monkeypatch, tmp_path) -> None:
    """The refusal tells the user to retry with --file, so the CSV has to survive."""
    csv_path = tmp_path / "utxo_dump_tmp.csv"
    monkeypatch.setattr(paths, "UTXO_TMP_CSV", csv_path)
    monkeypatch.setattr(utxo_updater.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(
        utxo_updater, "_run_utxo_dump", lambda out: out.write_text("address,amount\n")
    )

    def _refuse(*a, **k):
        raise RuntimeError("import implausível")

    monkeypatch.setattr(utxo_updater, "_do_import_locked", _refuse)

    with pytest.raises(RuntimeError):
        utxo_updater._dump_and_import("t", 0, tmp_path / "utxo.db", node_was_running=False)

    assert csv_path.exists(), "o CSV que a mensagem manda reusar foi apagado"


# ═══════════════════════════════════════════════════════════════════════════════
#  A rebuild records what it cost
# ═══════════════════════════════════════════════════════════════════════════════
#  The panel used to size its progress bar from the chainstate: 12.66 GB read as
#  the size of a database that came out at 3.12 GB.  Dividing by that number
#  ended the import phase about a quarter full, which reads as a rebuild that
#  stalled with no way to tell it apart from one that did.

def test_a_finished_import_records_its_real_sizes(tmp_path, monkeypatch) -> None:
    from mining_dark import utxo_updater
    from mining_dark.utils.utxo_db import UTXODatabase

    csv = tmp_path / "dump.csv"
    csv.write_text("address,amount\n" + "".join(
        f"1Addr{i},{1000 + i}\n" for i in range(50)))
    target = tmp_path / "utxo.db"

    utxo_updater._do_import_locked(csv, "test", 0, target)

    with UTXODatabase(target) as db:
        assert int(db.get_meta("last_csv_bytes")) == csv.stat().st_size
        assert int(db.get_meta("last_db_bytes")) > 0


def test_a_vanished_file_is_recorded_as_nothing(tmp_path) -> None:
    """One stat() in a try, so a file removed mid-rebuild cannot raise."""
    from mining_dark.utxo_updater import _file_size

    assert _file_size(tmp_path / "never-existed") == 0


# ----- reading the record back ----------------------------------------------
def test_the_estimate_prefers_a_measured_rebuild(tmp_path, monkeypatch) -> None:
    from mining_dark.gui.services import _last_rebuild_sizes
    from mining_dark.utils.utxo_db import UTXODatabase

    target = tmp_path / "utxo.db"
    db = UTXODatabase.create(target)
    db.set_meta("last_csv_bytes", "8860000000")
    db.set_meta("last_db_bytes", "3120000000")
    db.finalize()
    db.close()

    assert _last_rebuild_sizes(target) == (8_860_000_000, 3_120_000_000)


def test_an_unmeasured_database_reports_nothing(tmp_path) -> None:
    """The first rebuild ever, or one from before the sizes were recorded."""
    from mining_dark.gui.services import _last_rebuild_sizes
    from mining_dark.utils.utxo_db import UTXODatabase

    target = tmp_path / "utxo.db"
    db = UTXODatabase.create(target)
    db.finalize()
    db.close()

    assert _last_rebuild_sizes(target) == (0, 0)


def test_an_unreadable_database_cannot_break_the_panel(tmp_path) -> None:
    """This only sharpens an estimate; it must never take the readout down."""
    from mining_dark.gui.services import _last_rebuild_sizes

    broken = tmp_path / "utxo.db"
    broken.write_bytes(b"not a database at all")

    assert _last_rebuild_sizes(broken) == (0, 0)


def test_a_missing_database_reports_nothing(tmp_path) -> None:
    from mining_dark.gui.services import _last_rebuild_sizes

    assert _last_rebuild_sizes(tmp_path / "nowhere.db") == (0, 0)


def _disk(tmp_path, monkeypatch, chainstate_bytes: int):
    """probe_disk with a chainstate of a known size and a real db file."""
    from mining_dark.gui import services

    monkeypatch.setattr(services, "_directory_size", lambda _p: chainstate_bytes)

    class _Utxo:
        @staticmethod
        def resolved_db_file():
            return tmp_path / "utxo.db"

    class _Settings:
        utxo = _Utxo()

    return services.probe_disk(_Settings())


def test_without_a_measurement_the_ratio_is_used(tmp_path, monkeypatch) -> None:
    """
    A database comes out at roughly a quarter of the chainstate it was dumped
    from - 12.66 GB produced 3.12 GB on mainnet.  The old code used the
    chainstate size itself, which is where the 4x error came from.
    """
    from mining_dark.utils.utxo_db import UTXODatabase

    db = UTXODatabase.create(tmp_path / "utxo.db")
    db.finalize()
    db.close()

    disk = _disk(tmp_path, monkeypatch, 12_660_000_000)

    assert disk.estimated_db_bytes == pytest.approx(3_165_000_000, rel=0.01)


def test_a_measured_rebuild_overrides_the_ratio(tmp_path, monkeypatch) -> None:
    from mining_dark.utils.utxo_db import UTXODatabase

    db = UTXODatabase.create(tmp_path / "utxo.db")
    db.set_meta("last_csv_bytes", "8000000000")
    db.set_meta("last_db_bytes", "2900000000")
    db.finalize()
    db.close()

    disk = _disk(tmp_path, monkeypatch, 12_660_000_000)

    assert disk.estimated_db_bytes == 2_900_000_000
    assert disk.estimated_csv_bytes == 8_000_000_000


def test_no_node_falls_back_to_the_current_database(tmp_path, monkeypatch) -> None:
    """With no chainstate to measure, what is already there is the best guess."""
    from mining_dark.utils.utxo_db import UTXODatabase

    db = UTXODatabase.create(tmp_path / "utxo.db")
    db.finalize()
    db.close()
    size = (tmp_path / "utxo.db").stat().st_size

    disk = _disk(tmp_path, monkeypatch, 0)

    assert disk.estimated_db_bytes == size


# ═══════════════════════════════════════════════════════════════════════════════
#  A killed rebuild leaves temp files; the next one must not inherit them
# ═══════════════════════════════════════════════════════════════════════════════
def test_stale_temps_are_discarded_before_a_rebuild(tmp_path, monkeypatch) -> None:
    """
    The cleanup lives in a `finally`, which SIGKILL does not run.

    Left in place, the half-built .tmp.db was read by the panel as this
    rebuild's import progress - the bar opened on the wrong phase, frozen at
    the byte count the dead run reached, for the whole of the export.
    """
    from mining_dark import paths, utxo_updater

    csv = tmp_path / "utxo_dump_tmp.csv"
    monkeypatch.setattr(paths, "UTXO_TMP_CSV", csv)
    target = tmp_path / "utxo.db"
    target.write_bytes(b"the live database, untouched")
    csv.write_bytes(b"x" * 1_000)
    (tmp_path / "utxo.tmp.db").write_bytes(b"x" * 2_000)

    utxo_updater._discard_stale_temps(target)

    assert not csv.exists()
    assert not (tmp_path / "utxo.tmp.db").exists()
    assert target.read_bytes() == b"the live database, untouched"


def test_discarding_nothing_is_not_an_error(tmp_path, monkeypatch) -> None:
    from mining_dark import paths, utxo_updater

    monkeypatch.setattr(paths, "UTXO_TMP_CSV", tmp_path / "utxo_dump_tmp.csv")

    utxo_updater._discard_stale_temps(tmp_path / "utxo.db")   # must not raise
