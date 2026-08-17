"""
`mining-dark doctor` - the one command that answers "is this working?".

The checks return data rather than printed text, so every branch below runs
without a terminal, a node, or a datadir.
"""

from __future__ import annotations

import pytest

from mining_dark import doctor
from mining_dark.doctor import FAIL, OK, WARN
from mining_dark.i18n import set_language


# ----- bitcoin.conf ----------------------------------------------------------
def _conf(tmp_path, monkeypatch, body: str) -> None:
    from mining_dark import bitcoin_node, paths

    monkeypatch.setattr(paths, "BITCOIN_CORE_DIR", tmp_path)
    (tmp_path / "bitcoin.conf").write_text(body)
    assert bitcoin_node.conf_values()          # guard: the file is readable


def test_a_missing_conf_fails(tmp_path, monkeypatch) -> None:
    from mining_dark import paths

    monkeypatch.setattr(paths, "BITCOIN_CORE_DIR", tmp_path / "nowhere")

    check = doctor._node_config()

    assert check.status == FAIL
    assert "setup_bitcoin_core" in check.fix


def test_a_tight_prune_warns(tmp_path, monkeypatch) -> None:
    """
    2048 MiB is what produced the false "Corrupted block database".

    Core could not keep the undo data for the tip and for the historical blocks
    an assumeutxo background sync downloads at the same time.
    """
    _conf(tmp_path, monkeypatch, "prune=2048\nchecklevel=1\n")

    check = doctor._node_config()

    assert check.status == WARN
    assert "2048" in check.detail
    assert check.fix


def test_a_deep_checklevel_on_a_pruned_node_warns(tmp_path, monkeypatch) -> None:
    _conf(tmp_path, monkeypatch, "prune=20000\nchecklevel=3\n")

    check = doctor._node_config()

    assert check.status == WARN
    assert "checklevel" in check.fix


def test_a_pruned_node_without_checklevel_warns(tmp_path, monkeypatch) -> None:
    """Core defaults to 3, so saying nothing is the same as asking for 3."""
    _conf(tmp_path, monkeypatch, "prune=20000\nserver=1\n")

    assert doctor._node_config().status == WARN


def test_the_recommended_pruned_conf_passes(tmp_path, monkeypatch) -> None:
    _conf(tmp_path, monkeypatch, "prune=20000\nchecklevel=1\nserver=1\n")

    check = doctor._node_config()

    assert check.status == OK
    assert "20000" in check.detail


def test_a_full_node_is_not_asked_for_a_checklevel(tmp_path, monkeypatch) -> None:
    """Undo data is never missing without pruning, so level 3 is fine there."""
    _conf(tmp_path, monkeypatch, "prune=0\nserver=1\n")

    assert doctor._node_config().status == OK


def test_comments_do_not_become_settings(tmp_path, monkeypatch) -> None:
    from mining_dark import bitcoin_node

    _conf(tmp_path, monkeypatch, "# prune=2048 was the old value\nprune=20000\n")

    assert bitcoin_node.prune_target_mib() == 20_000


# ----- the snapshot chainstate ----------------------------------------------
@pytest.mark.parametrize(
    "state, status",
    [("loaded", OK), ("none", OK), ("loading", WARN), ("orphaned", FAIL)],
)
def test_every_snapshot_state_is_classified(monkeypatch, state, status) -> None:
    from mining_dark import bitcoin_node

    monkeypatch.setattr(bitcoin_node, "snapshot_dir_state", lambda: state)

    assert doctor._snapshot_state().status == status


# ----- next step -------------------------------------------------------------
def test_a_failure_outranks_a_warning() -> None:
    """Nothing later can work around a failure, so it is what to do first."""
    checks = [
        doctor.Check("primeiro", WARN, "", "conserte o aviso"),
        doctor.Check("segundo", FAIL, "", "conserte a falha"),
    ]

    assert doctor._next_step(checks) == "conserte a falha"


def test_warnings_are_taken_in_workflow_order() -> None:
    """The checks are already listed in the order the steps happen."""
    checks = [
        doctor.Check("no", WARN, "parado", "mining-dark node start"),
        doctor.Check("banco", WARN, "velho", "mining-dark utxo update"),
    ]

    assert doctor._next_step(checks) == "mining-dark node start"


def test_a_check_with_no_fix_is_not_offered_as_a_step() -> None:
    checks = [doctor.Check("disco", WARN, "não foi possível medir")]

    assert doctor._next_step(checks) == ""


def test_nothing_to_do_when_everything_passes() -> None:
    assert doctor._next_step([doctor.Check("tudo", OK, "certo")]) == ""


# ----- the report ------------------------------------------------------------
def test_a_report_with_only_warnings_is_healthy() -> None:
    """Halfway through setup is the normal state, not an error for a script."""
    report = doctor.Report(checks=[doctor.Check("no", WARN, "parado", "start")])

    assert report.healthy is True
    assert report.warned and not report.failed


def test_a_report_with_a_failure_is_not_healthy() -> None:
    report = doctor.Report(checks=[doctor.Check("bin", FAIL, "faltando", "setup")])

    assert report.healthy is False


# ═══════════════════════════════════════════════════════════════════════════════
#  Could a wallet found right now actually be written?
# ═══════════════════════════════════════════════════════════════════════════════
#  Everything else the doctor looks at is reproducible - a chainstate resyncs, a
#  database rebuilds, a snapshot downloads again.  A found key is not, so this
#  is the one check worth doing by actually writing.

def _settings_with(found_dir):
    class _Output:
        @staticmethod
        def resolved_found_wallets_dir():
            return found_dir

    class _Settings:
        output = _Output()

    return _Settings()


def test_a_writable_directory_passes(tmp_path) -> None:
    check = doctor._found_wallets(_settings_with(tmp_path / "found"))

    assert check.status == OK
    assert "0 já salvas" in check.detail


def test_the_directory_is_created_if_missing(tmp_path) -> None:
    """First run: nothing is there yet, and that must not read as a failure."""
    target = tmp_path / "nope" / "found"

    doctor._found_wallets(_settings_with(target))

    assert target.is_dir()


def test_saved_wallets_are_counted(tmp_path) -> None:
    target = tmp_path / "found"
    target.mkdir()
    for i in range(3):
        (target / f"wallet_2026010{i}_120000_1Addr.txt").write_text("x")

    assert "3 já salvas" in doctor._found_wallets(_settings_with(target)).detail


def test_the_wording_follows_the_active_language(tmp_path) -> None:
    """
    The status code is stable, the prose is not - that is the whole contract.

    A script reads `status`; a person reads `detail`, and it has to arrive in
    the language the CLI was told to speak.
    """
    settings = _settings_with(tmp_path / "found")

    set_language("en")
    english = doctor._found_wallets(settings)

    assert english.status == OK
    assert "0 already saved" in english.detail
    assert english.name == "Found wallets"


def test_a_read_only_directory_fails(tmp_path) -> None:
    """
    The failure that matters: the scan runs, finds something, and cannot save.

    `os.access` would not catch every shape of this, which is why the check
    writes a probe file instead of asking.
    """
    import os

    target = tmp_path / "found"
    target.mkdir()
    os.chmod(target, 0o500)
    try:
        check = doctor._found_wallets(_settings_with(target))
    finally:
        os.chmod(target, 0o700)

    assert check.status == FAIL
    assert check.fix


def test_the_probe_file_does_not_survive(tmp_path) -> None:
    """A stray file in the wallets folder would show up as a found wallet."""
    target = tmp_path / "found"

    doctor._found_wallets(_settings_with(target))

    assert list(target.iterdir()) == []


def test_a_world_writable_directory_warns(tmp_path) -> None:
    """
    The saved files are 0600, so their contents are safe either way.

    A directory anyone can write to is the real problem: it lets someone else
    replace a saved wallet with one of their own.
    """
    import os

    target = tmp_path / "found"
    target.mkdir()
    os.chmod(target, 0o707)
    try:
        check = doctor._found_wallets(_settings_with(target))
    finally:
        os.chmod(target, 0o700)

    assert check.status == WARN
    assert "chmod 700" in check.fix


# ═══════════════════════════════════════════════════════════════════════════════
#  The chain against the network
# ═══════════════════════════════════════════════════════════════════════════════
#  Every other node check compares the node with itself.  A chainstate that had
#  lost a coin sat six days at height 961,897 while the network reached 962,745,
#  and `doctor` said "at tip" the whole time: Core reports `headers` as the best
#  *valid* header chain, so with the branch above the tip marked invalid both
#  numbers read equal and nothing looked wrong.

def _chain_check(monkeypatch, *, running=True, info=None, branch=None, peers=0):
    from mining_dark import bitcoin_node, doctor

    monkeypatch.setattr(bitcoin_node, "is_running", lambda: running)
    monkeypatch.setattr(bitcoin_node, "getblockchaininfo", lambda: info)
    monkeypatch.setattr(bitcoin_node, "invalid_branch", lambda: branch)
    monkeypatch.setattr(bitcoin_node, "peer_block_height", lambda: peers)
    return doctor._chain_progress()


def test_an_invalid_branch_above_the_tip_fails(monkeypatch) -> None:
    """The exact shape of the incident: tip and headers agree, chain is dead."""
    from mining_dark import doctor

    check = _chain_check(
        monkeypatch,
        info={"blocks": 961_897, "headers": 961_897, "initialblockdownload": False},
        branch=(961_903, "0000dead", 6),
        peers=962_745,
    )

    assert check.status == doctor.FAIL
    assert "961,903" in check.detail
    assert "0000dead" in check.fix, "the fix has to carry the hash to reconsider"


def test_a_node_that_thinks_it_is_synced_while_the_network_moved_on_fails(
    monkeypatch,
) -> None:
    from mining_dark import doctor

    check = _chain_check(
        monkeypatch,
        info={"blocks": 961_897, "headers": 961_897, "initialblockdownload": False},
        peers=962_745,
    )

    assert check.status == doctor.FAIL
    assert "848" in check.detail


def test_downloading_behind_the_network_is_not_a_fault(monkeypatch) -> None:
    """Being behind during IBD is the job; _node_process already reports it."""
    from mining_dark import doctor

    check = _chain_check(
        monkeypatch,
        info={"blocks": 936_579, "headers": 961_897, "initialblockdownload": True},
        peers=962_755,
    )
    assert check.status == doctor.OK


def test_ordinary_propagation_lag_is_not_a_stall(monkeypatch) -> None:
    from mining_dark import doctor

    check = _chain_check(
        monkeypatch,
        info={"blocks": 962_744, "headers": 962_744, "initialblockdownload": False},
        peers=962_745,
    )
    assert check.status == doctor.OK


def test_the_check_stays_quiet_with_nothing_to_compare(monkeypatch) -> None:
    """A stopped node, a silent RPC or no peers must not manufacture a failure."""
    from mining_dark import doctor

    assert _chain_check(monkeypatch, running=False).status == doctor.OK
    assert _chain_check(monkeypatch, info=None).status == doctor.OK
    assert _chain_check(
        monkeypatch,
        info={"blocks": 961_897, "headers": 961_897, "initialblockdownload": False},
        peers=0,
    ).status == doctor.OK
