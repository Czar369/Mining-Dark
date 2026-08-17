"""UIState / EventBus behaviour - no Dear PyGui, no window, no display."""

from __future__ import annotations

import pytest

from mining_dark.gui.state import (
    AddressEvent,
    DatabaseEvent,
    DBStatus,
    EventBus,
    FoundEvent,
    LogEvent,
    LogLevel,
    NodeEvent,
    RunState,
    RunStateEvent,
    StatsEvent,
    UIState,
    WorkerEvent,
    WorkerStatus,
    abbreviate,
    fit_address,
    guess_address_type,
)


# ----- EventBus --------------------------------------------------------------
def test_bus_drains_in_order() -> None:
    bus = EventBus()
    bus.emit(LogEvent(LogLevel.INFO, "a"))
    bus.emit(LogEvent(LogLevel.ERROR, "b"))

    drained = bus.drain()
    assert [e.message for e in drained] == ["a", "b"]
    assert bus.drain() == []


def test_bus_drops_oldest_instead_of_blocking() -> None:
    """A full bus must never stall the scanner - it sheds the stalest event."""
    bus = EventBus(maxsize=3)
    for i in range(6):
        bus.emit(LogEvent(LogLevel.INFO, str(i)))

    drained = bus.drain()
    assert len(drained) == 3
    assert [e.message for e in drained] == ["3", "4", "5"]
    assert bus.dropped == 3


def test_bus_drain_respects_max_items() -> None:
    bus = EventBus()
    for i in range(10):
        bus.emit(LogEvent(LogLevel.INFO, str(i)))

    assert len(bus.drain(max_items=4)) == 4
    assert len(bus.drain()) == 6


# ----- UIState folding -------------------------------------------------------
def test_apply_folds_every_event_kind() -> None:
    state = UIState(worker_count=4)
    state.apply([
        StatsEvent(keys_generated=100, wallets_found=1, total_found_satoshis=50_000),
        WorkerEvent(2, WorkerStatus.VERIFYING, checked=7),
        LogEvent(LogLevel.SUCCESS, "hit"),
        AddressEvent("bc1qexample", "p2wpkh"),
        FoundEvent("1Address", "p2pkh", 50_000),
        RunStateEvent(RunState.RUNNING),
        DatabaseEvent(status=DBStatus.OK, address_count=42),
    ])

    assert state.stats.keys_generated == 100
    assert state.workers[2].status is WorkerStatus.VERIFYING
    assert state.workers[2].checked == 7
    assert [line.message for line in state.logs] == ["hit"]
    assert state.recent[0].address == "bc1qexample"
    assert state.found[0].btc == 0.0005
    assert state.run_state is RunState.RUNNING
    assert state.database.address_count == 42


def test_worker_event_out_of_range_is_ignored() -> None:
    """Resizing the worker slider must not let stale events index off the end."""
    state = UIState(worker_count=2)
    state.apply([WorkerEvent(9, WorkerStatus.SCANNING, checked=1)])
    assert all(w.status is WorkerStatus.WAITING for w in state.workers)


def test_set_worker_count_preserves_existing_rows() -> None:
    state = UIState(worker_count=3)
    state.apply([WorkerEvent(1, WorkerStatus.SCANNING, checked=9)])

    state.set_worker_count(6)
    assert len(state.workers) == 6
    assert state.workers[1].checked == 9
    assert state.workers[5].status is WorkerStatus.WAITING

    state.set_worker_count(2)
    assert len(state.workers) == 2
    assert state.workers[1].checked == 9


def test_logs_are_bounded() -> None:
    state = UIState(worker_count=1, max_log_lines=5)
    state.apply([LogEvent(LogLevel.INFO, str(i)) for i in range(20)])

    assert len(state.logs) == 5
    assert len(state.pending_logs) <= 5


def test_reset_counters_clears_session_data() -> None:
    state = UIState(worker_count=2)
    state.apply([
        StatsEvent(keys_generated=10),
        FoundEvent("1A", "p2pkh", 1),
        AddressEvent("1B", "p2pkh"),
        WorkerEvent(0, WorkerStatus.SCANNING, checked=5),
        LogEvent(LogLevel.INFO, "keep me"),
    ])
    state.reset_counters()

    assert state.stats.keys_generated == 0
    assert state.found == []
    assert len(state.recent) == 0
    assert state.workers[0].status is WorkerStatus.WAITING
    # Logs survive a restart on purpose - they are the session's history.
    assert len(state.logs) == 1


def test_hms_reads_as_a_clock() -> None:
    from mining_dark.gui.state import hms

    assert hms(3_725.0) == "01:02:05"
    assert hms(0.0) == "00:00:00"
    # Hours are not wrapped at 24 - a scan left running for days must say so.
    assert hms(98_045.0) == "27:14:05"


def test_the_clock_measures_the_scan_not_the_window() -> None:
    """
    It reads 00:00:00 until START is pressed, on purpose: the number is there
    to say how long the counters beside it took to get where they are, so it
    has to start when they do.
    """
    state = UIState(worker_count=4)
    assert state.session_hms() == "00:00:00"

    state.apply([StatsEvent(elapsed_seconds=3_725.0)])
    assert state.session_hms() == "01:02:05"


def test_a_new_scan_restarts_the_clock() -> None:
    state = UIState(worker_count=2)
    state.apply([StatsEvent(elapsed_seconds=3_725.0)])
    state.reset_counters()

    assert state.session_hms() == "00:00:00"


# ----- helpers ---------------------------------------------------------------
def test_abbreviate_only_shortens_long_addresses() -> None:
    assert abbreviate("1short") == "1short"
    long = "bc1q" + "a" * 50
    assert abbreviate(long, 10, 6) == "bc1qaaaaaa...aaaaaa"


def test_guess_address_type() -> None:
    assert guess_address_type("1" + "a" * 33) == "p2pkh"
    assert guess_address_type("3" + "a" * 33) == "p2sh_p2wpkh"
    assert guess_address_type("bc1q" + "a" * 38) == "p2wpkh"
    assert guess_address_type("bc1q" + "a" * 58) == "p2wsh"
    assert guess_address_type("bc1p" + "a" * 58) == "p2tr"
    assert guess_address_type("nonsense") == "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
#  A probe that could not reach the RPC must not erase what was known
# ═══════════════════════════════════════════════════════════════════════════════
#  probe_node returns every numeric field at zero when the RPC does not answer -
#  roughly one probe in twenty against a syncing node.  Those zeros mean "nobody
#  asked successfully", not "the node has no blocks", and taking them at face
#  value made the whole node section blink to zero about once a minute.

def _reachable(**kw) -> NodeEvent:
    base = dict(available=True, running=True, reachable=True, chain="main",
                blocks=960_187, headers=961_882, progress=0.9962,
                header_progress=1.0, header_height=961_882, ibd=True,
                snapshot_active=True)
    base.update(kw)
    return NodeEvent(**base)


def _unreachable() -> NodeEvent:
    """Exactly what probe_node emits when bitcoind is up but the RPC is busy."""
    return NodeEvent(available=True, running=True, reachable=False)


def test_a_busy_rpc_keeps_the_last_known_figures() -> None:
    state = UIState()
    state.apply([_reachable()])

    state.apply([_unreachable()])

    assert state.node.blocks == 960_187
    assert state.node.headers == 961_882
    assert state.node.progress == 0.9962
    assert state.node.snapshot_active is True


def test_a_busy_rpc_still_reports_itself_as_unreachable() -> None:
    """The figures are kept, but the panel must not be told the RPC answered."""
    state = UIState()
    state.apply([_reachable()])

    state.apply([_unreachable()])

    assert state.node.reachable is False
    assert state.node.running is True


def test_a_stopped_node_does_reset_the_readouts() -> None:
    """`running` comes from a pid check, so a node that is down is not a guess."""
    state = UIState()
    state.apply([_reachable()])

    state.apply([NodeEvent(available=True, running=False)])

    assert state.node.blocks == 0
    assert state.node.running is False


def test_a_node_that_never_answered_is_not_given_figures() -> None:
    """A node still warming up has nothing on record worth keeping."""
    state = UIState()

    state.apply([_unreachable()])

    assert state.node.blocks == 0
    assert state.node.reachable is False


def test_a_fresh_answer_replaces_the_kept_figures() -> None:
    state = UIState()
    state.apply([_reachable()])
    state.apply([_unreachable()])

    state.apply([_reachable(blocks=961_882, ibd=False, progress=0.99999)])

    assert state.node.blocks == 961_882
    assert state.node.ibd is False


# ═══════════════════════════════════════════════════════════════════════════════
#  Numbers read the same everywhere
# ═══════════════════════════════════════════════════════════════════════════════
def test_thousands_use_us_grouping() -> None:
    """
    Both languages, deliberately.

    Everything the panel sits beside writes numbers this way - the CLI,
    bitcoin-cli, debug.log, every block explorer - so translating them here
    only made one figure look like two different numbers depending on where it
    was read.
    """
    from mining_dark.gui.panels.common import group_thousands

    assert group_thousands(56_593_861) == "56,593,861"
    assert group_thousands(961_897) == "961,897"
    assert group_thousands(0) == "0"


def test_the_language_does_not_change_the_grouping() -> None:
    from mining_dark.i18n import set_language
    from mining_dark.gui.panels.common import group_thousands

    set_language("pt")
    in_pt = group_thousands(1_234_567)
    set_language("en")

    assert in_pt == group_thousands(1_234_567) == "1,234,567"


def test_no_number_is_translated_after_formatting() -> None:
    """
    The pt-BR form came from swapping "," for "." once the string was built.

    Correct for integers, silently wrong with a decimal point: 1,234.5 became
    "1.234.5", where the last separator claims to be another thousands group.
    That is how the UTXO database showed 3,115.2 MB as "3.115.2 MB".
    """
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "src" / "mining_dark"
    offenders = [
        path.relative_to(src)
        for path in src.rglob("*.py")
        if 'replace(",", ".")' in path.read_text()
    ]

    assert offenders == []


# ═══════════════════════════════════════════════════════════════════════════════
#  Clearing the log has to reach the buffer, not just the screen
# ═══════════════════════════════════════════════════════════════════════════════
def test_clear_logs_empties_both_buffers() -> None:
    """
    `logs` is what the level filter re-renders from; `pending_logs` is what the
    next frame appends.  Leaving either full brings the "cleared" lines back.
    """
    state = UIState()
    state.apply([LogEvent(LogLevel.ERROR, f"linha {i}") for i in range(8)])
    assert state.logs and state.pending_logs

    state.clear_logs()

    assert list(state.logs) == []
    assert state.pending_logs == []


# ═══════════════════════════════════════════════════════════════════════════════
#  Worker bars: how far behind the busiest one
# ═══════════════════════════════════════════════════════════════════════════════
#  They used to show `(checked % 64) / 64`, a sawtooth.  With twenty workers
#  draining one queue at the same rate, all twenty showed the same number -
#  measured at 16%, 17%, 19% across the whole list - and none of it meant
#  anything.  A share of the leader turns the list into a stall detector.

def _with_checked(*counts: int) -> UIState:
    state = UIState(worker_count=len(counts))
    state.apply([WorkerEvent(i, WorkerStatus.VERIFYING, checked=c)
                 for i, c in enumerate(counts)])
    return state


def test_workers_keeping_up_are_all_full() -> None:
    """The healthy case has to read as healthy at a glance."""
    assert _with_checked(1_290, 1_290, 1_290).worker_shares() == [1.0, 1.0, 1.0]


def test_a_straggler_falls_visibly_behind() -> None:
    shares = _with_checked(1_000, 1_000, 250).worker_shares()

    assert shares == [1.0, 1.0, 0.25]


def test_a_stalled_worker_reads_as_zero() -> None:
    """A worker that never took a batch while the others worked."""
    assert _with_checked(800, 0).worker_shares() == [1.0, 0.0]


def test_nothing_checked_yet_is_all_zero() -> None:
    """
    Before the first key, and right after reset_counters().

    1.0 would claim every worker is keeping up before any has done anything.
    """
    assert _with_checked(0, 0, 0).worker_shares() == [0.0, 0.0, 0.0]


def test_the_worker_list_is_never_empty() -> None:
    """
    `set_worker_count` clamps to 1, so a zero-worker state cannot be built.

    `worker_shares` still tolerates an empty list - `max(..., default=0)` and a
    comprehension over nothing - but this is the invariant it actually meets.
    """
    assert UIState(worker_count=0).worker_shares() == [0.0]


def test_the_share_list_lines_up_with_the_worker_list() -> None:
    """The panel zips the two with strict=True, so lengths must always match."""
    state = _with_checked(5, 9, 1)

    assert len(state.worker_shares()) == len(state.workers)

    state.set_worker_count(7)
    assert len(state.worker_shares()) == 7


def test_reset_clears_the_shares() -> None:
    state = _with_checked(400, 800)

    state.reset_counters()

    assert state.worker_shares() == [0.0, 0.0]


# ═══════════════════════════════════════════════════════════════════════════════
#  Addresses are trimmed to what the column can actually show
# ═══════════════════════════════════════════════════════════════════════════════
#  Measured in the panel: the ADDRESS column is 356 px and the font is
#  monospace at 6.73 px a glyph, so 34- and 42-character formats fit whole and
#  the two 62-character bech32 ones do not.  Letting them overflow is not an
#  option - Dear PyGui clips with no ellipsis, so a cut address looks exactly
#  like a whole one.

_P2PKH = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"                       # 34
_P2WPKH = "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq"              # 42
_P2TR = "bc1p5cyxnuxmeuwuvkwfem96l0bqtu0kv7tzrxlvjqxu6qxsz5f9pqqs5k5v0"  # 61


def test_an_address_that_fits_is_untouched() -> None:
    """The common case at the panel's normal width: nothing is hidden."""
    assert fit_address(_P2PKH, 52) == _P2PKH
    assert fit_address(_P2WPKH, 52) == _P2WPKH


def test_an_address_exactly_at_the_budget_is_untouched() -> None:
    assert fit_address(_P2PKH, len(_P2PKH)) == _P2PKH


def test_a_long_address_is_trimmed_to_the_budget() -> None:
    trimmed = fit_address(_P2TR, 52)

    assert len(trimmed) == 52
    assert "..." in trimmed


def test_both_ends_survive_the_trim() -> None:
    """
    The prefix says which format it is; the tail is what anyone comparing a
    match looks at first.  The middle is what can go.
    """
    trimmed = fit_address(_P2TR, 40)

    assert trimmed.startswith("bc1p")
    assert trimmed.endswith(_P2TR[-5:])


def test_the_same_rule_applies_to_every_format() -> None:
    """No special-casing per address type - one budget, one function."""
    for address in (_P2PKH, _P2WPKH, _P2TR):
        assert len(fit_address(address, 40)) <= 40


def test_a_budget_too_small_for_an_ellipsis_still_returns_something() -> None:
    assert fit_address(_P2TR, 2) == _P2TR[:2]


def test_a_zero_budget_does_not_erase_the_address() -> None:
    """A width that could not be measured must not blank the column."""
    assert fit_address(_P2TR, 0) == _P2TR
