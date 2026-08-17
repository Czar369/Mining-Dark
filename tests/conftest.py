"""
Shared fixtures.

The interface language is process-global state, and both the CLI and the GUI
read it to build the text they hand back.  A test that asserts on that text
would otherwise pass or fail on whatever language the test *before* it happened
to leave selected - which is exactly how three utxo_updater tests started
failing when the CLI began honouring the setting.
"""

from __future__ import annotations

import pytest

from mining_dark.i18n import DEFAULT_LANGUAGE, set_language


@pytest.fixture(autouse=True)
def pinned_language():
    """Start every test from the default language, and leave it that way."""
    set_language(DEFAULT_LANGUAGE)
    yield
    set_language(DEFAULT_LANGUAGE)
