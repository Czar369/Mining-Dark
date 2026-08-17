"""Loguru configuration.  Private keys are NEVER written to log files."""

from __future__ import annotations

import re
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional, Union

from loguru import logger

from mining_dark import paths

# Word boundaries (\b) are useless here: '_' counts as a word character, so
# '\bKxxx\b' never matches inside 'wallet_Kxxx'.  Every pattern below instead
# asserts that the neighbouring character does not belong to the key's own
# alphabet, which is what "this token stands alone" actually means.

# 64+ hex chars = raw private key.  The open-ended {64,} also catches a key
# embedded in a longer hex run, where a fixed {64} window would find no
# boundary on either side.
_RE_HEX_KEY = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64,}(?![0-9a-fA-F])")

# WIF encoded private keys:
#   - Uncompressed mainnet: starts with '5', 51 chars
#   - Compressed mainnet:   starts with 'K' or 'L', 52 chars
#   - Testnet:              starts with '9' (uncompressed) or 'c' (compressed)
_RE_WIF_KEY = re.compile(
    r"(?<![1-9A-HJ-NP-Za-km-z])("
    r"5[1-9A-HJ-NP-Za-km-z]{50}"      # uncompressed mainnet
    r"|[KL][1-9A-HJ-NP-Za-km-z]{51}"  # compressed mainnet
    r"|9[1-9A-HJ-NP-Za-km-z]{50}"     # uncompressed testnet
    r"|c[1-9A-HJ-NP-Za-km-z]{51}"     # compressed testnet
    r")(?![1-9A-HJ-NP-Za-km-z])"
)

# BIP32 extended private keys (111 chars).  A single xprv controls the whole
# derivation tree, so it leaks more than any individual WIF - and the HD
# generator works with exactly this material.
_RE_XPRV_KEY = re.compile(
    r"(?<![1-9A-HJ-NP-Za-km-z])"
    r"(?:xprv|yprv|zprv|tprv|uprv|vprv)"  # mainnet / SLIP-132 / testnet
    r"[1-9A-HJ-NP-Za-km-z]{100,}"
    r"(?![1-9A-HJ-NP-Za-km-z])"
)

# A BIP39 mnemonic is 12 or 24 words, but matching "12 lowercase words" as a
# regex both misses real seeds (any separator other than a single space, any
# capitalisation) and swallows ordinary English prose.  Membership in the
# actual wordlist is the property that distinguishes the two.
_MNEMONIC_MIN_WORDS = 12
_RE_WORD = re.compile(r"[A-Za-z]+")


@lru_cache(maxsize=1)
def _bip39_words() -> frozenset:
    """The English BIP39 wordlist, loaded once on first use."""
    from mnemonic import Mnemonic

    return frozenset(Mnemonic("english").wordlist)


def _mnemonic_spans(text: str) -> list:
    """Character spans of every run of >= 12 consecutive BIP39 words."""
    words = _bip39_words()
    spans: list = []
    run: list = []

    for match in _RE_WORD.finditer(text):
        if match.group(0).lower() in words:
            run.append(match)
            continue
        if len(run) >= _MNEMONIC_MIN_WORDS:
            spans.append((run[0].start(), run[-1].end()))
        run = []

    if len(run) >= _MNEMONIC_MIN_WORDS:
        spans.append((run[0].start(), run[-1].end()))
    return spans


#: Filename patterns this module owns.  The sweep below deletes only these -
#: `*` at the end covers the `.gz` that `compression` leaves behind.
_LOG_PATTERNS: tuple = ("scanner_*.log*", "errors_*.log*")

_HOUR = 3600.0
_DAY = 24 * _HOUR

_DURATION_SECONDS: dict = {
    "hour": _HOUR,
    "day": _DAY,
    "week": 7 * _DAY,
    # A month is taken as thirty days, the same convention loguru uses.
    "month": 30 * _DAY,
}

_RE_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-z]+?)s?\s*$", re.IGNORECASE)


def _parse_retention(retention: str) -> Optional[float]:
    """
    `"7 days"` -> 604800.0.  None when the string is not a plain age.

    Deliberately narrower than loguru's own parser: this only has to read what
    `config.yaml` documents, and returning None for anything else means the
    sweep declines rather than guesses at deleting the wrong files.
    """
    match = _RE_DURATION.match(retention or "")
    if match is None:
        return None
    seconds = _DURATION_SECONDS.get(match.group(2).lower())
    return None if seconds is None else float(match.group(1)) * seconds


def sweep_old_logs(log_path: Path, retention: str) -> int:
    """
    Delete our own log files older than `retention`.  Returns how many went.

    Loguru's `retention=` cannot do this job here.  Its cleanup runs inside
    `FileSink._terminate_file` under `if is_rotating or rotation is None` - so
    with a size-based `rotation=` configured, retention only ever runs when a
    file actually reaches that size.  These files are small and most are empty,
    none has ever hit 50 MB, so retention had never run once: measured at 230
    files past the 7-day window, some three months old.

    Doing it here instead means it runs on every invocation, whether or not a
    rotation happens, and it is by mtime rather than by the timestamp in the
    name - a rotated or compressed file keeps its own age either way.
    """
    max_age = _parse_retention(retention)
    if max_age is None:
        return 0

    cutoff = time.time() - max_age
    removed = 0
    for pattern in _LOG_PATTERNS:
        for path in log_path.glob(pattern):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                # A file another process holds open, or a permission problem.
                # Housekeeping must never be the reason a scan fails to start.
                continue
    return removed


def setup_logger(
    level: str = "INFO",
    logs_dir: Optional[Union[str, Path]] = None,
    rotation: str = "50 MB",
    retention: str = "7 days",
) -> None:
    """
    Configure Loguru with:
      - Coloured stdout output
      - Rotating file sink (INFO+)
      - Separate error file sink (WARNING+)
    """
    log_path = Path(logs_dir) if logs_dir else paths.LOGS_DIR
    log_path.mkdir(parents=True, exist_ok=True)

    logger.remove()

    # Before the sinks exist, so the files this run is about to open are never
    # candidates for their own sweep.
    sweep_old_logs(log_path, retention)

    # diagnose=False on every sink: loguru's default renders each frame's local
    # variables into the traceback, so a private key held in a local would reach
    # the log even when the exception message is clean - which _no_secret_filter
    # cannot catch, since it only scans the message and the exception value, not
    # the rendered frame locals.  Turning diagnose off removes that whole class
    # of leak; backtrace stays on, so the stack trace is still there to debug by.
    logger.add(
        sys.stdout,
        level=level,
        colorize=True,
        format=(
            "<green>{time:HH:mm:ss}</green> "
            "<level>{level: <8}</level> "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        filter=_no_secret_filter,
        diagnose=False,
    )

    # delay=True on both file sinks: without it loguru opens the file the
    # moment the sink is added, so every invocation - `doctor` and `--help`
    # included - left a `scanner_*.log` and an `errors_*.log` behind whether or
    # not anything was ever logged to them.  That is where 688 of the 690 files
    # in data/logs/ came from.  Delayed, a file appears only once there is a
    # line to put in it - so an errors_ file on disk means the run actually
    # produced a warning or worse, not merely that the run happened.
    logger.add(
        log_path / "scanner_{time}.log",
        level="INFO",
        rotation=rotation,
        retention=retention,
        compression="gz",
        encoding="utf-8",
        delay=True,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{line} - {message}",
        filter=_no_secret_filter,
        diagnose=False,
    )

    logger.add(
        log_path / "errors_{time}.log",
        level="WARNING",
        rotation="10 MB",
        retention=retention,
        compression="gz",
        encoding="utf-8",
        delay=True,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{line} - {message}",
        filter=_no_secret_filter,
        diagnose=False,
    )


def contains_secret(message: str) -> bool:
    """
    True if `message` looks like it carries secret material - a raw hex private
    key, a WIF encoded private key, or a BIP39 mnemonic.

    Shared by the file sinks and by the graphical dashboard, so both apply the
    exact same rule to anything they are about to persist or display.
    """
    return bool(
        _RE_HEX_KEY.search(message)
        or _RE_WIF_KEY.search(message)
        or _RE_XPRV_KEY.search(message)
        or _mnemonic_spans(message)
    )


def redact(text: str, keep: int = 4) -> str:
    """
    Mask any secret material in `text`, keeping a few characters at each end.

    Used by the GUI's wallet preview: enough of the key stays visible to tell
    two wallets apart, never enough to spend from either.
    """
    def _mask_value(value: str) -> str:
        if keep <= 0 or len(value) <= keep * 2 + 4:
            return "*" * len(value)
        return f"{value[:keep]}{'*' * (len(value) - keep * 2)}{value[-keep:]}"

    def _mask(match: re.Match) -> str:
        return _mask_value(match.group(0))

    # Masking preserves length, so spans found before any substitution stay
    # valid afterwards.  Mnemonics go first because '*' is not a word
    # character and would otherwise break the runs apart.
    for start, end in reversed(_mnemonic_spans(text)):
        text = f"{text[:start]}{_mask_value(text[start:end])}{text[end:]}"

    for pattern in (_RE_HEX_KEY, _RE_WIF_KEY, _RE_XPRV_KEY):
        text = pattern.sub(_mask, text)
    return text


def _no_secret_filter(record: dict) -> bool:
    """
    Masks secret material in place, keeping the record itself.

    Dropping the whole record used to take the diagnostic down with the
    secret - an OS error message that happened to carry key-shaped text
    vanished from every sink, errors log included, with nothing to show it
    had ever been written.  Redaction removes the secret and nothing else.

    This is a safety net; callers should not log secrets in the first place.
    """
    message = record.get("message", "")
    if contains_secret(message):
        record["message"] = redact(message)

    # A traceback is the other way key material reaches a sink, and it bypasses
    # the message entirely.  Loguru's diagnose=True prints the local variables
    # of every frame, so one `logger.exception` inside the wallet writer would
    # dump the key that failed to save.  Dropping the exception rather than
    # redacting it: the formatted traceback is rebuilt from the exception
    # object, so masking the rendered text would not stick.
    if record.get("exception") and _exception_carries_secret(record["exception"]):
        record["exception"] = None
        record["message"] = f"{record.get('message', '')} [traceback omitido: continha material de chave]"

    return True


def _exception_carries_secret(exception) -> bool:
    """Whether an exception's own text would expose key material."""
    value = getattr(exception, "value", None)
    if value is None:
        return False
    try:
        return contains_secret(f"{value!r} {value}")
    except Exception:  # noqa: BLE001 - a broken __repr__ must not break logging
        return True
