"""Pydantic v2 settings - loads from YAML file, resolves paths against `paths` module."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mining_dark import paths
# These errors fire while the config is still being read, so the language they
# come out in is the default (or whatever `--lang` set) - the file that would
# have chosen one is the file that failed to load.
from mining_dark.i18n import t

_VALID_ADDRESS_TYPES = {
    "p2pkh",
    "p2pkh_uncompressed",
    "p2sh_p2wpkh",
    "p2wpkh",
    "p2wsh",
    "p2tr",
}


class _ConfigModel(BaseModel):
    """
    Base for every config section.

    validate_assignment is what makes the validators below worth having: almost
    nothing builds these models from a complete dict.  The CLI flags, the
    interactive menu and the GUI settings panel all assign onto an existing
    Settings, and without this the checks only ever ran at load time.  That is
    how `--workers 0` reached the scanner untouched and produced a run that
    generated keys while checking no addresses at all.
    """

    model_config = ConfigDict(validate_assignment=True)


class ScannerConfig(_ConfigModel):
    mode: str = "random"  # "random" | "hd"
    workers: int = 10
    queue_size: int = 500
    address_types: list[str] = [
        "p2pkh",
        "p2pkh_uncompressed",
        "p2sh_p2wpkh",
        "p2wpkh",
        "p2wsh",
        "p2tr",
    ]
    min_balance_satoshis: int = 0

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        if v not in {"random", "hd"}:
            raise ValueError(f"mode must be 'random' or 'hd', got '{v}'")
        return v

    @field_validator("workers")
    @classmethod
    def _validate_workers(cls, v: int) -> int:
        if not 1 <= v <= 512:
            raise ValueError(f"workers must be between 1 and 512, got {v}")
        return v

    @field_validator("address_types")
    @classmethod
    def _validate_address_types(cls, v: list[str]) -> list[str]:
        for t in v:
            if t not in _VALID_ADDRESS_TYPES:
                raise ValueError(f"Unknown address type '{t}'. Valid: {_VALID_ADDRESS_TYPES}")
        return v

    @field_validator("min_balance_satoshis")
    @classmethod
    def _validate_min_balance(cls, v: int) -> int:
        # The scan keeps a wallet when total_satoshis > min_balance.  That "> "
        # is the safeguard that stops the default 0 from matching every generated
        # key (which all have a zero balance until proven otherwise) - but a
        # negative value defeats it: 0 > -1 is true, so every address checked
        # would be "found" and written to disk.  Reject it at the edge.
        if v < 0:
            raise ValueError(f"min_balance_satoshis must be >= 0, got {v}")
        return v


class HDWalletConfig(_ConfigModel):
    derivation_paths: list[str] = [
        "m/44'/0'/0'/0/{i}",
        "m/49'/0'/0'/0/{i}",
        "m/84'/0'/0'/0/{i}",
        "m/86'/0'/0'/0/{i}",
    ]
    child_count: int = 20


class OutputConfig(_ConfigModel):
    """
    Output paths.  Empty string means "use the default from paths.py" -
    that keeps config.yaml minimal for users who don't want to override.
    """
    found_wallets_dir: str = ""
    save_csv: bool = True
    json_indent: int = 2

    def resolved_found_wallets_dir(self) -> Path:
        if self.found_wallets_dir:
            return Path(self.found_wallets_dir).expanduser().resolve()
        return paths.FOUND_WALLETS_DIR


#: Loguru's built-in severities.  Anything else reaches `logger.add(level=...)`
#: and raises there, inside _bootstrap - so a typo here took down every command
#: with a traceback instead of a message about the config file.
_VALID_LOG_LEVELS = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}


class LoggingConfig(_ConfigModel):
    level: str = "INFO"
    logs_dir: str = ""
    rotation: str = "50 MB"
    retention: str = "7 days"

    @field_validator("level")
    @classmethod
    def _validate_level(cls, v: str) -> str:
        if v.upper() not in _VALID_LOG_LEVELS:
            raise ValueError(
                f"level must be one of {sorted(_VALID_LOG_LEVELS)}, got '{v}'"
            )
        return v.upper()

    def resolved_logs_dir(self) -> Path:
        if self.logs_dir:
            return Path(self.logs_dir).expanduser().resolve()
        return paths.LOGS_DIR


class UTXOConfig(_ConfigModel):
    """
    Optional override for the UTXO database path.  Same convention as
    OutputConfig: empty string -> use paths.py default.

    There is deliberately no bitcoin_core_dir here.  `bitcoin_node` always
    drives the node with `-datadir=paths.BITCOIN_CORE_DIR`, so the setting that
    used to live here changed nothing while the GUI displayed it as if it were
    in effect.  MINING_DARK_DATA_DIR relocates the whole data tree at once.
    """
    db_file: str = ""

    def resolved_db_file(self) -> Path:
        if self.db_file:
            return Path(self.db_file).expanduser().resolve()
        return paths.UTXO_DB_FILE


class UIConfig(_ConfigModel):
    refresh_fps: int = 4
    recent_table_rows: int = 15
    theme: str = "dark"

    # ----- appearance --------------------------------------------------------
    # Interface language only, and it drives both surfaces: the window reads it
    # at startup and can switch live, the CLI reads it in `_bootstrap`.
    # Everything the project *generates* - wallet files, log files, CSV headers
    # - stays in English regardless.
    language: str = "pt"
    # Accent palette: matrix | amber | ice
    palette: str = "matrix"
    # Font size multiplier for HiDPI screens
    font_scale: float = 1.0

    @field_validator("language")
    @classmethod
    def _validate_language(cls, v: str) -> str:
        if v not in {"pt", "en"}:
            raise ValueError(f"language must be 'pt' or 'en', got '{v}'")
        return v

    @field_validator("palette")
    @classmethod
    def _validate_palette(cls, v: str) -> str:
        if v not in {"matrix", "amber", "ice"}:
            raise ValueError(f"palette must be matrix, amber or ice, got '{v}'")
        return v

    @field_validator("font_scale")
    @classmethod
    def _validate_font_scale(cls, v: float) -> float:
        if not 0.6 <= v <= 2.5:
            raise ValueError(f"font_scale must be between 0.6 and 2.5, got {v}")
        return v


class Settings(_ConfigModel):
    scanner:   ScannerConfig  = Field(default_factory=ScannerConfig)
    hd_wallet: HDWalletConfig = Field(default_factory=HDWalletConfig)
    output:    OutputConfig   = Field(default_factory=OutputConfig)
    logging:   LoggingConfig  = Field(default_factory=LoggingConfig)
    utxo:      UTXOConfig     = Field(default_factory=UTXOConfig)
    ui:        UIConfig       = Field(default_factory=UIConfig)


class ConfigError(Exception):
    """A config file exists but cannot be used.  Carries a message for the user."""


def describe_validation_error(exc: ValidationError, separator: str = "; ") -> str:
    """
    Turn a pydantic error into something worth showing a person.

    `str(ValidationError)` carries the model name, the input repr and a link to
    the pydantic docs - none of which tells the user which setting to fix.
    """
    return separator.join(
        f"{'.'.join(str(part) for part in err['loc'])}: "
        f"{err['msg'].removeprefix('Value error, ')}"
        for err in exc.errors()
    )


def load_settings(config_path: Optional[Path | str] = None) -> Settings:
    """
    Load settings from a YAML file, falling back to defaults if not found.
    Default location: <project_root>/config.yaml

    Raises ConfigError - never a raw ParserError or ValidationError - so the
    callers can show the problem instead of a traceback.
    """
    explicit = config_path is not None
    path = Path(config_path) if config_path else paths.CONFIG_FILE

    if not path.exists():
        if explicit:
            # Silently running on defaults here means the user's settings are
            # ignored for the whole session with nothing saying so.
            raise ConfigError(t("config.err.missing", path=path))
        return Settings()

    try:
        with open(path, encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}
    except OSError as exc:
        raise ConfigError(t("config.err.unreadable", path=path, error=exc)) from None
    except yaml.YAMLError as exc:
        raise ConfigError(t("config.err.bad_yaml", path=path, error=exc)) from None

    if not isinstance(raw, dict):
        raise ConfigError(
            t("config.err.not_a_mapping", path=path, got=type(raw).__name__)
        )

    try:
        return Settings.model_validate(raw)
    except ValidationError as exc:
        problems = describe_validation_error(exc, separator="\n  ")
        raise ConfigError(t("config.err.bad_values", path=path, problems=problems)) from None


# ═══════════════════════════════════════════════════════════════════════════════
#  Writing config.yaml back out
# ═══════════════════════════════════════════════════════════════════════════════
# A plain yaml.dump would silently throw away every comment in config.yaml, and
# those comments are the file's documentation.  Since this module owns the
# schema it can regenerate them, so a round-trip through the settings dialog
# leaves the file just as readable as it was hand-written.
_FILE_HEADER = """\
# ═══════════════════════════════════════════════════════════════════════════════
#  Mining-Dark - Configuration
#
#  Written by `mining-dark gui` (Settings dialog) and read by every command.
#  Safe to edit by hand - the GUI reloads it on open.
#
#  All paths are resolved by the `mining_dark.paths` module.  Leaving path
#  fields blank ("") uses the defaults inside <project_root>/data/.
#  Override MINING_DARK_DATA_DIR to relocate every data folder at once.
# ═══════════════════════════════════════════════════════════════════════════════
"""

_SECTION_DOCS: dict[str, str] = {
    "scanner": """\
# ----- Key generation and balance checking -----------------------------------
#   mode                 "random" (aleatorias) | "hd" (BIP32/44/49/84/86)
#   workers              async workers checking balances in parallel (1-512)
#   queue_size           internal key queue - memory vs throughput
#   address_types        p2pkh | p2pkh_uncompressed | p2sh_p2wpkh
#                        p2wpkh | p2wsh | p2tr
#   min_balance_satoshis only save wallets above this confirmed balance
""",
    "hd_wallet": """\
# ----- HD mode only (scanner.mode = "hd") ------------------------------------
#   derivation_paths     templates; {i} is replaced by the child index
#   child_count          child keys derived per master seed (BIP44 gap = 20)
""",
    "output": """\
# ----- Where found wallets are written ---------------------------------------
#   found_wallets_dir    empty = data/found_wallets/
#   save_csv             also append to a rolling summary.csv
#
#   WARNING: these files contain the PRIVATE KEYS of anything found.
""",
    "logging": """\
# ----- Loguru sinks ----------------------------------------------------------
#   level                TRACE | DEBUG | INFO | WARNING | ERROR
#   logs_dir             empty = data/logs/
""",
    "utxo": """\
# ----- Local UTXO database ---------------------------------------------------
#   db_file              empty = data/utxo/utxo.db
#
#   The Bitcoin Core datadir is not configurable here: the node always runs
#   with -datadir=data/bitcoin-core/.  Set MINING_DARK_DATA_DIR to move the
#   whole data/ tree, node included.
""",
    "ui": """\
# ----- Interfaces ------------------------------------------------------------
#   refresh_fps          terminal dashboard refresh rate
#   recent_table_rows    rows in the terminal "recent addresses" table
#   theme                terminal dashboard: "dark" | "light"
#   language             interface language, window and CLI: "pt" | "en"
#                        (generated files stay in English either way)
#   palette              GUI accent: "matrix" | "amber" | "ice"
#   font_scale           GUI font multiplier for HiDPI screens
""",
}

_SECTION_ORDER = ("scanner", "hd_wallet", "output", "logging", "utxo", "ui")


_UNKNOWN_SECTION_DOC = """\
# ----- Not managed by Mining-Dark --------------------------------------------
#   Sections this version does not recognise, preserved as they were found.
"""


def _unmodelled_entries(path: Path, known: dict) -> dict:
    """
    Everything in the file on disk that `known` does not describe.

    Pydantic ignores unknown keys when loading, so regenerating the file from
    the models alone silently dropped them: a hand-written note, an option from
    a newer version, a section belonging to a fork.  Reading them back here
    makes the save non-destructive without the schema having to model them.
    """
    if not path.exists():
        return {}

    try:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError):
        return {}

    if not isinstance(raw, dict):
        return {}

    extras: dict = {}
    for section, values in raw.items():
        if section not in known:
            extras[section] = values
        elif isinstance(values, dict) and isinstance(known[section], dict):
            unknown = {k: v for k, v in values.items() if k not in known[section]}
            if unknown:
                extras[section] = unknown
    return extras


def save_settings(
    settings: Settings,
    config_path: Optional[Path | str] = None,
    *,
    backup: bool = True,
) -> Path:
    """
    Write `settings` back to YAML, regenerating the documentation comments.

    Keys and sections outside the schema are carried over untouched - pydantic
    drops them on load, so emitting only what the models know about used to
    delete anything the user had added by hand.  Comments the *user* wrote are
    still replaced by the generated ones; only the values survive.

    The write is atomic (temp file + replace) so an interrupted save can never
    leave a half-written config behind.  The first save also drops a one-time
    `.bak` beside the original.
    """
    path = Path(config_path) if config_path else paths.CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    if backup and path.exists():
        backup_path = path.with_name(path.name + ".bak")
        if not backup_path.exists():
            shutil.copy2(path, backup_path)

    data = settings.model_dump(mode="python")
    extras = _unmodelled_entries(path, data)

    chunks: list[str] = [_FILE_HEADER]
    for section in _SECTION_ORDER:
        if section not in data:
            continue
        values = data[section]
        leftover = extras.pop(section, None)
        if leftover and isinstance(values, dict):
            values = {**values, **leftover}

        chunks.append(_SECTION_DOCS.get(section, ""))
        chunks.append(
            yaml.safe_dump(
                {section: values},
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
                width=100,
            )
        )

    # Whole sections this schema knows nothing about, kept verbatim.
    if extras:
        chunks.append(_UNKNOWN_SECTION_DOC)
        chunks.append(
            yaml.safe_dump(
                extras,
                sort_keys=False,
                allow_unicode=True,
                default_flow_style=False,
                width=100,
            )
        )

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(chunks), encoding="utf-8")
    tmp.replace(path)
    return path
