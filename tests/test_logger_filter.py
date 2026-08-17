"""The Loguru filter must mask key material without losing the log record."""

from __future__ import annotations

import pytest

from mining_dark.utils.logger import _no_secret_filter, contains_secret, redact

_HEX = "9ba0f7148686b71f671d0aa01a9327f216d49368d41cee599e9fcdc72298ebf0"
_WIF_COMPRESSED = "KwDiBf89QgGbjEhKnhXJuH7LrciVrZi3qYjgd9M7rFU73sVHnoWn"
_WIF_UNCOMPRESSED = "5HpHagT65TZzG1PH3CSu63k8DbpvD8s5ip4nEB3kEsreAnchuDf"
_XPRV = (
    "xprv9s21ZrQH143K3QTDL4LXw2F7HEK3wJUD2nW2nRk4stbPy6cq3jPPqjiChkVvv"
    "NKmPGJxWUtg6LnF5kejMRNNU3TGtRBeJgk33yuGBxrMPHi"
)
_MNEMONIC = "abandon " * 11 + "about"


def _record(msg: str) -> dict:
    return {"message": msg}


def _filtered(msg: str) -> str:
    """Run a message through the sink filter and return what would be written."""
    record = _record(msg)
    assert _no_secret_filter(record) is True
    return record["message"]


# ═══════════════════════════════════════════════════════════════════════════════
#  Secrets must never reach a sink
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "message, secret",
    [
        (f"Look: {_HEX}", _HEX),
        # '_' is a word character, so \b never fired inside these two.
        (f"privkey_{_HEX} found", _HEX),
        (f"{_HEX}_backup", _HEX),
        # A key embedded in a longer hex run has no boundary on either side.
        (f"deadbeef{_HEX}cafe", _HEX),
        (f"WIF={_WIF_COMPRESSED}", _WIF_COMPRESSED),
        (f"WIF={_WIF_UNCOMPRESSED}", _WIF_UNCOMPRESSED),
        (f"wallet_{_WIF_COMPRESSED}", _WIF_COMPRESSED),
        # One xprv controls the whole derivation tree.
        (f"master {_XPRV}", _XPRV),
    ],
)
def test_key_material_is_masked(message: str, secret: str) -> None:
    assert contains_secret(message)
    assert secret not in _filtered(message)


@pytest.mark.parametrize(
    "separator",
    [" ", "\n", "  ", ", ", "\t"],
)
def test_mnemonic_is_masked_whatever_the_separator(separator: str) -> None:
    """Only single-space lowercase seeds used to be caught."""
    words = _MNEMONIC.split()
    message = f"seed: {separator.join(words)}"

    assert contains_secret(message)
    assert "abandon abandon" not in _filtered(message).lower()


def test_capitalised_mnemonic_is_masked() -> None:
    message = f"seed: {_MNEMONIC.capitalize()}"

    assert contains_secret(message)
    assert "abandon" not in _filtered(message).lower()


# ═══════════════════════════════════════════════════════════════════════════════
#  Everything else must survive intact
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "message",
    [
        "worker started",
        # A bitcoin address is public, not a secret.
        "saved 1BgGZ9tcN4rm9KBzDn7KprQz87SZ26SAMH",
        # Twelve or more ordinary English words are not a seed phrase.  These
        # used to be dropped from every sink, errors log included.
        "a rebuild replaces the old database it is only deleted at the end "
        "along with the other files",
        "worker telemetry hook failed cannot open the local utxo database "
        "file at the configured path now",
        "scan finished with no wallets found after checking many addresses "
        "in the local utxo database now",
    ],
)
def test_ordinary_messages_pass_through_unchanged(message: str) -> None:
    assert not contains_secret(message)
    assert _filtered(message) == message


def test_the_record_is_kept_not_dropped() -> None:
    """Redaction removes the secret; the diagnostic around it stays readable."""
    written = _filtered(f"Failed to save wallet: cannot write {_HEX} to disk")

    assert _HEX not in written
    assert written.startswith("Failed to save wallet: cannot write ")
    assert written.endswith(" to disk")


def test_redaction_is_idempotent() -> None:
    """Each sink filters the same record, so masking must not compound."""
    once = redact(f"privkey_{_HEX}")

    assert redact(once) == once
    assert not contains_secret(once)


def test_exception_traceback_never_leaks_a_frame_local_key(tmp_path) -> None:
    """
    End to end through the real sinks: loguru's default diagnose renders every
    frame's local variables into the traceback, so a private key held in a local
    would reach the log even though the exception message is clean - a path the
    message filter cannot see.  setup_logger must disable diagnose; this proves a
    logged exception with a WIF in scope writes the trace but not the key.
    """
    from loguru import logger

    from mining_dark.core.key_generator import KeyGenerator
    from mining_dark.utils.logger import setup_logger

    wif = KeyGenerator.get_wif(KeyGenerator.generate_private_key(), compressed=True)

    setup_logger(level="INFO", logs_dir=tmp_path)
    try:
        def _save(secret: str) -> None:      # the WIF lives only in a frame local
            raise OSError("disk full")       # ... never in the exception message

        try:
            _save(wif)
        except OSError:
            logger.exception("failed to persist found wallet")
    finally:
        logger.remove()                       # flush and detach the file sinks

    written = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.log"))
    assert "Traceback" in written             # the stack trace is still recorded
    assert wif not in written                 # but the key never appears


# ═══════════════════════════════════════════════════════════════════════════════
#  Log files have to exist, hold the session, and get cleaned up
# ═══════════════════════════════════════════════════════════════════════════════
def test_no_file_is_created_until_something_is_logged(tmp_path) -> None:
    """
    Every invocation used to leave a scanner_ and an errors_ file behind whether
    or not anything was written - 688 of the 690 files in data/logs/ were 0
    bytes.  `delay=True` is what stops that.
    """
    from loguru import logger

    from mining_dark.utils.logger import setup_logger

    setup_logger(level="INFO", logs_dir=tmp_path)
    try:
        assert list(tmp_path.glob("*.log")) == []
    finally:
        logger.remove()


def test_an_errors_file_appears_only_when_something_goes_wrong(tmp_path) -> None:
    """An errors_ file on disk has to mean something actually failed."""
    from loguru import logger

    from mining_dark.utils.logger import setup_logger

    setup_logger(level="INFO", logs_dir=tmp_path)
    try:
        logger.info("nothing wrong here")
        assert list(tmp_path.glob("scanner_*.log"))
        assert list(tmp_path.glob("errors_*.log")) == []

        logger.error("something broke")
        assert list(tmp_path.glob("errors_*.log"))
    finally:
        logger.remove()


@pytest.mark.parametrize(
    "text, seconds",
    [
        ("7 days", 604_800.0),
        ("1 day", 86_400.0),
        ("2 weeks", 1_209_600.0),
        ("12 hours", 43_200.0),
        ("forever", None),
        ("50 MB", None),        # a rotation string must never read as an age
        ("", None),
    ],
)
def test_retention_parsing(text, seconds) -> None:
    from mining_dark.utils.logger import _parse_retention

    assert _parse_retention(text) == seconds


def test_old_logs_are_swept_and_recent_ones_survive(tmp_path) -> None:
    """
    Loguru's own retention only runs when a rotation fires, and these files
    never reach the size threshold - so it had never run once.  The sweep in
    setup_logger is what actually enforces the window.
    """
    import os
    import time

    from loguru import logger

    from mining_dark.utils.logger import setup_logger

    old = tmp_path / "scanner_2026-01-01_00-00-00_000000.log"
    old_gz = tmp_path / "errors_2026-01-01_00-00-00_000000.log.gz"
    fresh = tmp_path / "scanner_2026-08-16_09-00-00_000000.log"
    stranger = tmp_path / "notes.txt"          # not ours - must not be touched

    for path in (old, old_gz, fresh, stranger):
        path.write_text("x", encoding="utf-8")

    ancient = time.time() - 30 * 86_400
    for path in (old, old_gz, stranger):
        os.utime(path, (ancient, ancient))

    setup_logger(level="INFO", logs_dir=tmp_path, retention="7 days")
    logger.remove()

    assert not old.exists()
    assert not old_gz.exists()
    assert fresh.exists()
    assert stranger.exists()


def test_sweep_declines_when_retention_is_not_an_age(tmp_path) -> None:
    """An unparseable window must delete nothing, not everything."""
    import os
    import time

    from mining_dark.utils.logger import sweep_old_logs

    old = tmp_path / "scanner_2026-01-01_00-00-00_000000.log"
    old.write_text("x", encoding="utf-8")
    ancient = time.time() - 30 * 86_400
    os.utime(old, (ancient, ancient))

    assert sweep_old_logs(tmp_path, "forever") == 0
    assert old.exists()


def test_the_stream_log_panel_and_the_log_file_hold_the_same_lines(tmp_path) -> None:
    """
    The GUI feeds its panel from the EventBus, which never touched loguru - so
    a graphical session wrote nothing to disk and a scan that failed overnight
    lost its only log when the window closed.

    `_mirror_logs` reads nothing off `self`, so this drives the real method
    without opening a window.
    """
    pytest.importorskip("dearpygui.dearpygui")

    from loguru import logger

    import mining_dark.gui.app as gui_app
    from mining_dark.core.key_generator import KeyGenerator
    from mining_dark.gui.state import AddressEvent, LogEvent, LogLevel
    from mining_dark.utils.logger import setup_logger

    wif = KeyGenerator.get_wif(KeyGenerator.generate_private_key(), compressed=True)
    events = [
        LogEvent(LogLevel.INFO, "scan iniciado"),
        AddressEvent("bc1qexample", "p2wpkh"),        # not a log line - skipped
        LogEvent(LogLevel.WARNING, "fila cheia"),     # .value is "WARN", not a
        LogEvent(LogLevel.ERROR, "backend caiu"),     # level loguru knows
        LogEvent(LogLevel.SUCCESS, f"achou {wif}"),
    ]

    setup_logger(level="DEBUG", logs_dir=tmp_path)
    try:
        gui_app.MiningDarkGUI._mirror_logs(object(), events)
    finally:
        logger.remove()

    written = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("scanner_*.log"))
    for message in ("scan iniciado", "fila cheia", "backend caiu", "achou"):
        assert message in written
    assert "bc1qexample" not in written           # only LogEvents are mirrored
    assert wif not in written                     # the sink filter still applies

    errors = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("errors_*.log"))
    assert "backend caiu" in errors
    assert "scan iniciado" not in errors


def test_every_stream_log_level_maps_to_a_real_loguru_level() -> None:
    """A level missing from the table would raise mid-frame, once per line."""
    pytest.importorskip("dearpygui.dearpygui")

    from loguru import logger

    import mining_dark.gui.app as gui_app
    from mining_dark.gui.state import LogLevel

    assert set(gui_app._FILE_LEVEL) == set(LogLevel)
    for name in gui_app._FILE_LEVEL.values():
        assert logger.level(name)                 # raises if loguru lacks it


def test_a_normal_shutdown_does_not_land_in_the_errors_file(tmp_path) -> None:
    """
    The STREAM LOG paints a stop yellow so it stands out on screen.  Mirroring
    that severity straight into loguru put "Scan ended" into errors_*.log next
    to real faults, so every clean session produced an errors file - which is
    exactly how a real one stops being noticed.  `file_level` splits the two.
    """
    pytest.importorskip("dearpygui.dearpygui")

    from loguru import logger

    import mining_dark.gui.app as gui_app
    from mining_dark.gui.state import LogEvent, LogLevel

    events = [
        # Routine: yellow on screen, INFO on disk.
        LogEvent(LogLevel.WARNING, "Scan ended - 213,062 keys",
                 file_level=LogLevel.INFO),
        # A genuine warning keeps its severity in both places.
        LogEvent(LogLevel.WARNING, "UTXO database is 40 days old"),
    ]

    setup = __import__("mining_dark.utils.logger", fromlist=["setup_logger"])
    setup.setup_logger(level="INFO", logs_dir=tmp_path)
    try:
        gui_app.MiningDarkGUI._mirror_logs(object(), events)
    finally:
        logger.remove()

    scanner = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("scanner_*.log"))
    errors = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("errors_*.log"))

    # Both lines are in the session log; only the real warning is in errors_.
    assert "Scan ended" in scanner
    assert "40 days old" in scanner
    assert "Scan ended" not in errors
    assert "40 days old" in errors
