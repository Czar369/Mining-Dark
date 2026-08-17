"""
TaskRunner: one long operation at a time, and always an end event.

The panel disables buttons for as long as it believes a task is running, so a
task that ends without saying so leaves the interface permanently locked.
"""

from __future__ import annotations

from mining_dark.gui.services import TaskRunner
from mining_dark.gui.state import EventBus, TaskEvent


def _run(fn) -> list:
    bus = EventBus()
    runner = TaskRunner(bus)
    assert runner.submit("utxo-rebuild", fn) is True
    runner.join(timeout=5.0)
    return [e for e in bus.drain() if isinstance(e, TaskEvent)]


def test_a_task_that_finishes_reports_running_then_stopped() -> None:
    events = _run(lambda: None)

    assert [e.running for e in events] == [True, False]


def test_a_task_that_raises_still_reports_stopped() -> None:
    def boom() -> None:
        raise RuntimeError("no")

    events = _run(boom)

    assert [e.running for e in events] == [True, False]
    assert "no" in events[-1].detail


def test_a_refusal_still_reports_stopped() -> None:
    """
    The export pipeline is shared with the CLI, where refusing means exiting.

    SystemExit is a BaseException: it used to sail past `except Exception`, so
    the thread died with the panel still showing a rebuild in flight and every
    button disabled behind it until the window was restarted.
    """
    def refuse() -> None:
        raise SystemExit(1)

    events = _run(refuse)

    assert [e.running for e in events] == [True, False]
    assert events[-1].detail


def test_a_task_that_dies_on_a_bare_baseexception_still_reports_stopped() -> None:
    """
    SystemExit was only the first BaseException to expose the hole: any non-
    Exception exit (a stray KeyboardInterrupt, a custom BaseException) would
    otherwise skip the end event too, locking every button behind a task that
    no longer exists.  The catch-all must cover them all.
    """
    class Weird(BaseException):
        pass

    def boom() -> None:
        raise Weird("not an Exception, not a SystemExit")

    events = _run(boom)

    assert [e.running for e in events] == [True, False]


def test_the_runner_is_free_again_after_a_refusal() -> None:
    def refuse() -> None:
        raise SystemExit(1)

    bus = EventBus()
    runner = TaskRunner(bus)
    runner.submit("utxo-rebuild", refuse)
    runner.join(timeout=5.0)

    assert runner.busy is False
    assert runner.submit("node-start", lambda: None) is True


# ═══════════════════════════════════════════════════════════════════════════════
#  Starting past a pruned node's false "corrupt database"
# ═══════════════════════════════════════════════════════════════════════════════
def _start_harness(monkeypatch, *, fails_first: bool, pruned_cause: bool):
    """
    Stand in for bitcoind: the first start dies, a shallower one may succeed.

    Records every `check_level` it was asked for, which is what the retry is.
    """
    from mining_dark import bitcoin_node

    calls: list = []
    state = {"up": False}

    def fake_start(reindex: bool = False, check_level=None) -> None:
        calls.append(check_level)
        # The first attempt fails when asked to; a shallower one always works.
        state["up"] = not fails_first or check_level is not None

    monkeypatch.setattr(bitcoin_node, "start", fake_start)
    monkeypatch.setattr(bitcoin_node, "wait_until_ready", lambda **_kw: state["up"])
    monkeypatch.setattr(bitcoin_node, "is_running", lambda: state["up"])
    monkeypatch.setattr(bitcoin_node, "pruned_verify_failure", lambda: pruned_cause)
    monkeypatch.setattr(bitcoin_node, "last_startup_error", lambda *_a, **_k: "boom")
    return calls


def _levels(monkeypatch, **kw) -> list:
    from mining_dark.gui import services
    from mining_dark.gui.state import EventBus

    calls = _start_harness(monkeypatch, **kw)
    monkeypatch.setattr(services, "probe_node", lambda: None)
    services.start_node(EventBus())
    return calls


def test_a_healthy_start_is_not_retried(monkeypatch) -> None:
    assert _levels(monkeypatch, fails_first=False, pruned_cause=False) == [None]


def test_a_pruned_verify_failure_retries_shallower(monkeypatch) -> None:
    """
    The retry is the whole point: Core's own advice here is `-reindex`, which
    on a pruned node discards a working datadir to rebuild from blocks that
    pruning already deleted.
    """
    from mining_dark import bitcoin_node

    assert _levels(monkeypatch, fails_first=True, pruned_cause=True) == [
        None, bitcoin_node.SHALLOW_CHECK_LEVEL,
    ]


def test_an_ordinary_failure_is_not_retried(monkeypatch) -> None:
    """A real corruption must be reported, not quietly started around."""
    assert _levels(monkeypatch, fails_first=True, pruned_cause=False) == [None]
