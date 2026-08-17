"""Translations and the config.yaml round-trip behind the settings dialog."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pydantic import ValidationError

from mining_dark.config.settings import (
    ConfigError,
    Settings,
    describe_validation_error,
    load_settings,
    save_settings,
)
from mining_dark import i18n
from mining_dark.utils.logger import contains_secret, redact

_WIF = "KwDiBf89QgGbjEhKnhXJuH7LrciVrZi3qYjgd9M7rFU73sVHnoWn"
_HEX = "4c0883a69102937d6231471b5dbb6204fe512961708279cd0d1e2d4bd63e7f4f"


@pytest.fixture(autouse=True)
def _restore_language():
    """Language is process-global; never let one test leak into the next."""
    original = i18n.get_language()
    yield
    i18n.set_language(original)


# ═══════════════════════════════════════════════════════════════════════════════
#  i18n
# ═══════════════════════════════════════════════════════════════════════════════
def test_every_key_has_both_translations() -> None:
    for key, entry in i18n._STRINGS.items():
        assert len(entry) == 2, key
        assert entry[0].strip(), f"{key} has no Portuguese text"
        assert entry[1].strip(), f"{key} has no English text"


def test_placeholders_match_between_languages() -> None:
    """A `{name}` present in one language must exist in the other, or `t()` breaks."""
    import string

    def fields(text: str) -> set[str]:
        return {f for _, f, _, _ in string.Formatter().parse(text) if f}

    for key, (pt, en) in i18n._STRINGS.items():
        assert fields(pt) == fields(en), f"{key}: {fields(pt)} != {fields(en)}"


def test_t_switches_language() -> None:
    i18n.set_language("pt")
    assert i18n.t("btn.start") == "INICIAR"
    i18n.set_language("en")
    assert i18n.t("btn.start") == "START"


def test_t_formats_and_survives_bad_input() -> None:
    i18n.set_language("en")
    assert i18n.t("workers.active", active=3, total=8) == "3/8 ACTIVE"
    # A missing placeholder must not raise inside a render loop.
    assert i18n.t("workers.active") == "{active}/{total} ACTIVE"


def test_unknown_key_returns_itself() -> None:
    assert i18n.t("nope.not.here") == "nope.not.here"


def test_every_key_the_code_asks_for_exists() -> None:
    """
    A typo in a `t()` key is invisible at runtime.

    `t()` returns the key itself rather than raising, which is right for a
    render loop - a stray `doctor.mark.wanr` on screen beats a crash - but it
    means a misspelling ships silently and prints a dotted identifier at the
    user.  Only literal keys are checked; the two dynamic call sites build
    theirs from a status code and are covered by their own tests.
    """
    import re
    from pathlib import Path

    src = Path(i18n.__file__).parent
    literal_key = re.compile(r"""\bt\(\s*['"]([a-z][a-z0-9_.]*)['"]""")

    missing = [
        f"{path.relative_to(src)}: {key}"
        for path in sorted(src.rglob("*.py"))
        if path.name != "i18n.py"
        for key in literal_key.findall(path.read_text(encoding="utf-8"))
        if key not in i18n._STRINGS
    ]

    assert not missing, "keys used in code but absent from _STRINGS:\n  " + "\n  ".join(missing)


def test_unknown_language_falls_back() -> None:
    assert i18n.set_language("klingon") == i18n.DEFAULT_LANGUAGE


def test_hud_jargon_is_not_translated() -> None:
    """SCAN/VERIFY/STREAM LOG are pipeline vocabulary, identical in both."""
    for key in ("log.title", "tile.found"):
        pt, en = i18n._STRINGS[key]
        assert pt == en, key


# ═══════════════════════════════════════════════════════════════════════════════
#  config.yaml round-trip
# ═══════════════════════════════════════════════════════════════════════════════
def test_save_then_load_preserves_every_field(tmp_path: Path) -> None:
    settings = Settings()
    settings.scanner.mode = "hd"
    settings.scanner.workers = 37
    settings.scanner.address_types = ["p2wpkh", "p2tr"]
    settings.scanner.min_balance_satoshis = 5000
    settings.output.found_wallets_dir = "/tmp/wallets"
    settings.ui.language = "en"
    settings.ui.palette = "ice"
    settings.ui.font_scale = 1.25

    path = save_settings(settings, tmp_path / "config.yaml", backup=False)
    restored = load_settings(path)

    assert restored.scanner.mode == "hd"
    assert restored.scanner.workers == 37
    assert restored.scanner.address_types == ["p2wpkh", "p2tr"]
    assert restored.scanner.min_balance_satoshis == 5000
    assert restored.output.found_wallets_dir == "/tmp/wallets"
    assert restored.ui.language == "en"
    assert restored.ui.palette == "ice"
    assert restored.ui.font_scale == 1.25


def test_saved_file_keeps_its_documentation(tmp_path: Path) -> None:
    """The point of the custom dumper: comments survive a GUI save."""
    path = save_settings(Settings(), tmp_path / "config.yaml", backup=False)
    text = path.read_text(encoding="utf-8")

    assert "Mining-Dark - Configuration" in text
    assert "PRIVATE KEYS" in text          # the found_wallets warning
    assert text.count("# -----") >= 6      # one banner per section
    assert yaml.safe_load(text)["scanner"]["mode"] == "random"


def test_save_backs_up_the_original_once(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("scanner:\n  workers: 3\n", encoding="utf-8")

    save_settings(Settings(), path)
    backup = tmp_path / "config.yaml.bak"
    assert backup.exists()
    assert "workers: 3" in backup.read_text(encoding="utf-8")

    # A second save must not clobber the pristine backup.
    save_settings(Settings(), path)
    assert "workers: 3" in backup.read_text(encoding="utf-8")


def test_invalid_ui_values_are_rejected() -> None:
    with pytest.raises(ValueError):
        Settings.model_validate({"ui": {"language": "de"}})
    with pytest.raises(ValueError):
        Settings.model_validate({"ui": {"palette": "puce"}})
    with pytest.raises(ValueError):
        Settings.model_validate({"ui": {"font_scale": 9.0}})


def test_a_negative_min_balance_is_rejected_at_load() -> None:
    """
    The scan keeps a wallet when total_satoshis > min_balance, and every
    generated key starts at a zero balance.  A negative min_balance makes
    `0 > min_balance` true for all of them, so a hand-edited config.yaml would
    turn the scanner into a flood of zero-balance "found" wallets on disk.
    """
    with pytest.raises(ValueError):
        Settings.model_validate({"scanner": {"min_balance_satoshis": -1}})

    # Zero is the smallest legitimate value and must still load.
    assert Settings.model_validate(
        {"scanner": {"min_balance_satoshis": 0}}
    ).scanner.min_balance_satoshis == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  Validation on assignment
#
#  Nothing builds these models from a complete dict at runtime: the CLI flags,
#  the setup menu and the GUI dialog all assign onto an existing Settings.  With
#  validation only at load time, `--workers 0` reached the scanner and produced
#  a run that generated keys while checking no addresses at all.
# ═══════════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "section, field, value",
    [
        ("scanner", "workers", 0),
        ("scanner", "workers", -3),
        ("scanner", "workers", 999_999),
        ("scanner", "mode", "turbo"),
        ("scanner", "mode", ""),
        ("scanner", "min_balance_satoshis", -1),
        ("scanner", "address_types", ["p2wpkh", "not-a-type"]),
        ("ui", "language", "klingon"),
        ("ui", "palette", "puce"),
        ("ui", "font_scale", 9.0),
    ],
)
def test_assignment_is_validated(section: str, field: str, value: object) -> None:
    settings = Settings()
    before = getattr(getattr(settings, section), field)

    with pytest.raises(ValueError):
        setattr(getattr(settings, section), field, value)

    assert getattr(getattr(settings, section), field) == before, "valor ruim foi gravado"


def test_valid_assignment_still_works() -> None:
    settings = Settings()

    settings.scanner.workers = 32
    settings.scanner.mode = "hd"
    settings.scanner.address_types = ["p2wpkh", "p2tr"]

    assert settings.scanner.workers == 32
    assert settings.scanner.mode == "hd"
    assert settings.scanner.address_types == ["p2wpkh", "p2tr"]


# ═══════════════════════════════════════════════════════════════════════════════
#  Redaction used by the wallet preview
# ═══════════════════════════════════════════════════════════════════════════════
def test_redact_masks_keys_but_keeps_the_shape() -> None:
    text = f"hex : {_HEX}\nwif : {_WIF}\naddress : 1BitcoinEaterAddressDontSendf59kuE"
    masked = redact(text)

    assert _HEX not in masked
    assert _WIF not in masked
    assert not contains_secret(masked)
    # Enough remains to tell two wallets apart.
    assert masked.startswith("hex : 4c08")
    assert "1BitcoinEaterAddressDontSendf59kuE" in masked


def test_redact_leaves_ordinary_text_alone() -> None:
    text = "MINING-DARK - WALLET FOUND\nBALANCE\n  0.00004200 BTC"
    assert redact(text) == text


# ═══════════════════════════════════════════════════════════════════════════════
#  Interactive setup menu
# ═══════════════════════════════════════════════════════════════════════════════
def test_menu_reasks_until_the_value_is_in_range(monkeypatch) -> None:
    """IntPrompt has no bounds of its own, so the menu has to impose them."""
    from mining_dark.ui import setup_menu

    answers = iter([0, 999_999, 12])
    monkeypatch.setattr(setup_menu.IntPrompt, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr(setup_menu.console, "print", lambda *a, **k: None)

    assert setup_menu._ask_int_in_range("workers", default=10, low=1, high=512) == 12


def test_menu_leaves_configured_address_types_alone(monkeypatch) -> None:
    """The menu never asks about address types; it used to overwrite them anyway."""
    from rich.prompt import Confirm, IntPrompt, Prompt

    from mining_dark.ui import setup_menu

    monkeypatch.setattr(setup_menu, "_show_utxo_status", lambda *a, **k: True)
    monkeypatch.setattr(setup_menu.console, "print", lambda *a, **k: None)
    monkeypatch.setattr(setup_menu.console, "clear", lambda *a, **k: None)
    monkeypatch.setattr(setup_menu.console, "rule", lambda *a, **k: None)
    monkeypatch.setattr(Confirm, "ask", lambda *a, **k: True)
    monkeypatch.setattr(Prompt, "ask", lambda *a, **k: "1")     # random mode
    monkeypatch.setattr(IntPrompt, "ask", lambda *a, **k: 8)

    settings = Settings()
    settings.scanner.address_types = ["p2wpkh"]

    result = setup_menu.run_setup(settings)

    assert result.scanner.address_types == ["p2wpkh"]
    assert result.scanner.workers == 8


def test_menu_summary_lists_only_the_enabled_types(capsys) -> None:
    """The summary used to advertise all six regardless of the configuration."""
    from mining_dark.ui import setup_menu

    setup_menu._show_summary("random", 8, None, ["p2wpkh", "p2tr"])
    shown = capsys.readouterr().out

    assert "P2WPKH" in shown
    assert "P2TR" in shown
    assert "P2SH-P2WPKH" not in shown


# ═══════════════════════════════════════════════════════════════════════════════
#  A save must not delete what the schema cannot describe
# ═══════════════════════════════════════════════════════════════════════════════
def test_unknown_keys_and_sections_survive_a_save(tmp_path: Path) -> None:
    """
    Pydantic drops unknown keys on load, so regenerating the file from the
    models alone used to delete anything the user had added by hand.
    """
    path = tmp_path / "config.yaml"
    path.write_text(
        "scanner:\n"
        "  workers: 7\n"
        "  my_custom_option: mantenha-me\n"
        "my_future_section:\n"
        "  algo: importante\n",
        encoding="utf-8",
    )

    settings = load_settings(path)
    settings.scanner.workers = 11
    save_settings(settings, path, backup=False)

    written = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert written["scanner"]["workers"] == 11          # the edit landed
    assert written["scanner"]["my_custom_option"] == "mantenha-me"
    assert written["my_future_section"] == {"algo": "importante"}
    # And the result is still loadable.
    assert load_settings(path).scanner.workers == 11


def test_saving_a_fresh_config_adds_no_extras(tmp_path: Path) -> None:
    path = save_settings(Settings(), tmp_path / "config.yaml", backup=False)
    written = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert set(written) == {"scanner", "hd_wallet", "output", "logging", "utxo", "ui"}


# ═══════════════════════════════════════════════════════════════════════════════
#  A broken config must explain itself, not raise a traceback
# ═══════════════════════════════════════════════════════════════════════════════
def test_malformed_yaml_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("scanner:\n  workers: [nao: fecha\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="não é um YAML válido"):
        load_settings(path)


def test_invalid_value_names_the_setting(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("scanner:\n  workers: 99999\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="scanner.workers"):
        load_settings(path)


def test_an_explicit_config_that_is_missing_is_an_error(tmp_path: Path) -> None:
    """Falling back to defaults here would ignore the user's settings silently."""
    with pytest.raises(ConfigError, match="não existe"):
        load_settings(tmp_path / "nao_existe.yaml")


def test_a_missing_default_config_is_fine(tmp_path: Path, monkeypatch) -> None:
    """No config.yaml at all is a normal first run, not a failure."""
    from mining_dark import paths

    monkeypatch.setattr(paths, "CONFIG_FILE", tmp_path / "config.yaml")
    assert load_settings().scanner.workers == Settings().scanner.workers


def test_validation_errors_are_described_without_pydantic_noise() -> None:
    """str(ValidationError) carries the model name and a docs URL; users need neither."""
    settings = Settings()

    with pytest.raises(ValidationError) as caught:
        settings.scanner.workers = 0

    described = describe_validation_error(caught.value)

    assert described == "workers: workers must be between 1 and 512, got 0"
    assert "pydantic" not in described


def test_no_translation_key_is_defined_twice() -> None:
    """
    A repeated key is invisible: Python keeps the last value and the earlier
    entry silently stops existing.  It shipped a section header rendering as an
    unrelated status note, and nothing failed.
    """
    import re
    from pathlib import Path

    source = Path(i18n.__file__).read_text(encoding="utf-8")
    # Only the _STRINGS block: LANGUAGES above it uses the same shape.
    block = source.split("_STRINGS", 1)[1]
    keys = re.findall(r'^    "([a-z0-9_.]+)":', block, re.M)

    duplicates = {key for key in keys if keys.count(key) > 1}
    assert not duplicates, f"chaves definidas duas vezes: {sorted(duplicates)}"
    assert len(keys) == len(i18n._STRINGS)


# ═══════════════════════════════════════════════════════════════════════════════
#  RELOAD picks up hand edits
# ═══════════════════════════════════════════════════════════════════════════════
#  It re-reads config.yaml and pushes the result into the running app.  Language
#  and palette need the interface rebuilt, so each has its own callback - and
#  applying only one of them was the defect: a file that changed both left
#  `settings.ui.palette` naming colours the screen was not using.

class _FakeApp:
    """
    The part of MiningDarkGUI that reload touches, with the rebuilds stubbed.

    Real ones need a window; what matters here is which of them get called.
    """

    def __init__(self, settings, config_path) -> None:
        self.settings = settings
        self.config_path = config_path
        self.language = settings.ui.language
        self.palette_name = settings.ui.palette
        self.applied: list = []

    # --- the two rebuild paths, recorded instead of executed ---
    def _on_theme_change(self, name: str) -> None:
        if name not in ("matrix", "amber", "ice") or name == self.palette_name:
            return
        self.palette_name = name
        self.settings.ui.palette = name
        self.applied.append(("palette", name))

    def _on_language_change(self, code: str) -> None:
        if code == self.language:
            return
        self.language = code
        self.settings.ui.language = code
        self.applied.append(("language", code))

    # --- everything else reload calls, neutered ---
    def _adopt_settings(self) -> None:
        self.applied.append(("adopt", self.settings.scanner.workers))

    def _log(self, *_a) -> None: ...
    def _dialog_status(self, *_a, **_k) -> None: ...
    def _refresh_database(self) -> None: ...


def _reload(app) -> None:
    """Call the real `_on_reload_settings` against the stub."""
    from mining_dark.gui.app import MiningDarkGUI

    MiningDarkGUI._on_reload_settings(app)


def _write(path: Path, settings: Settings, **ui) -> None:
    save_settings(settings, path)
    raw = yaml.safe_load(path.read_text())
    raw["ui"].update(ui)
    path.write_text(yaml.safe_dump(raw))


def test_reload_picks_up_a_hand_edited_value(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    settings = Settings()
    settings.scanner.workers = 44
    _write(cfg, settings)
    app = _FakeApp(settings, cfg)

    raw = yaml.safe_load(cfg.read_text())
    raw["scanner"]["workers"] = 99
    cfg.write_text(yaml.safe_dump(raw))
    _reload(app)

    assert app.settings.scanner.workers == 99


def test_reload_applies_language_and_palette_together(tmp_path) -> None:
    """
    The defect: an if/elif applied whichever came first and dropped the other.

    A config edited in an editor - where changing two lines is one action -
    then left the palette recorded but not shown.
    """
    cfg = tmp_path / "config.yaml"
    settings = Settings()
    settings.ui.language = "en"
    settings.ui.palette = "amber"
    _write(cfg, settings, language="pt", palette="ice")
    app = _FakeApp(settings, cfg)
    app.language, app.palette_name = "en", "amber"

    _reload(app)

    assert app.palette_name == "ice"
    assert app.language == "pt"


def test_reload_applies_the_palette_before_the_language(tmp_path) -> None:
    """A language change rebuilds the tree; the colours must already be set."""
    cfg = tmp_path / "config.yaml"
    settings = Settings()
    settings.ui.language = "en"
    settings.ui.palette = "amber"
    _write(cfg, settings, language="pt", palette="ice")
    app = _FakeApp(settings, cfg)
    app.language, app.palette_name = "en", "amber"

    _reload(app)

    kinds = [kind for kind, _ in app.applied if kind in ("palette", "language")]
    assert kinds == ["palette", "language"]


def test_reload_changing_neither_rebuilds_nothing(tmp_path) -> None:
    cfg = tmp_path / "config.yaml"
    settings = Settings()
    settings.ui.language = "en"
    settings.ui.palette = "amber"
    _write(cfg, settings)
    app = _FakeApp(settings, cfg)

    _reload(app)

    assert [k for k, _ in app.applied if k != "adopt"] == []


def test_reload_survives_a_broken_config(tmp_path) -> None:
    """Hand edits are what reload is for, so a broken file is the expected case."""
    cfg = tmp_path / "config.yaml"
    settings = Settings()
    settings.scanner.workers = 44
    _write(cfg, settings)
    app = _FakeApp(settings, cfg)

    cfg.write_text("scanner: {workers: [this is not a number]}\n")
    _reload(app)                                   # must not raise

    assert app.settings.scanner.workers == 44      # the good values survive
