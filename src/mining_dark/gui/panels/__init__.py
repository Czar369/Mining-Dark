"""Dashboard panels - each owns one region of the window."""

from mining_dark.gui.panels.activity import ActivityPanel
from mining_dark.gui.panels.common import PanelContext
from mining_dark.gui.panels.footer import FooterCallbacks, FooterPanel
from mining_dark.gui.panels.header import HeaderPanel
from mining_dark.gui.panels.logs import LogPanel
from mining_dark.gui.panels.workers import WorkersPanel

__all__ = [
    "ActivityPanel",
    "FooterCallbacks",
    "FooterPanel",
    "HeaderPanel",
    "LogPanel",
    "PanelContext",
    "WorkersPanel",
]
