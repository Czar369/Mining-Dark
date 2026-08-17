"""
Mining-Dark graphical dashboard (Dear PyGui).

    from mining_dark.gui import run_gui
    run_gui(simulate=True)

Dear PyGui is an optional dependency:  pip install "mining-dark[gui]"
"""

from __future__ import annotations

__all__ = ["GUIUnavailableError", "ensure_available", "run_gui"]

_INSTALL_HINT = (
    "Dear PyGui nao esta instalado.\n"
    "  pip install 'mining-dark[gui]'      (ou)      pip install dearpygui"
)


class GUIUnavailableError(RuntimeError):
    """Raised when the GUI is requested but Dear PyGui is missing."""


def ensure_available() -> None:
    """Fail early with an actionable message instead of an ImportError trace."""
    try:
        import dearpygui.dearpygui  # noqa: F401
    except ImportError as exc:      # pragma: no cover - depends on the install
        raise GUIUnavailableError(_INSTALL_HINT) from exc


def run_gui(**kwargs):
    """Launch the dashboard.  See `mining_dark.gui.app.run_gui` for arguments."""
    ensure_available()
    from mining_dark.gui.app import run_gui as _run_gui
    return _run_gui(**kwargs)
