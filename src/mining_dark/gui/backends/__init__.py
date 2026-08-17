"""
Telemetry sources for the dashboard.

`SimulatedBackend` is imported eagerly (pure stdlib); `LiveBackend` is not,
because it pulls in the crypto stack - import it explicitly when you need it.
"""

from mining_dark.gui.backends.base import ScanBackend
from mining_dark.gui.backends.simulated import SimulatedBackend

__all__ = ["ScanBackend", "SimulatedBackend"]
