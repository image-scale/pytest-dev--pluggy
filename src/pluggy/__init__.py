# pluggy - A minimalist plugin system
"""Pluggy: plugin management and hook calling for python."""
from __future__ import annotations

__all__ = [
    "__version__",
    "HookCallError",
    "HookimplMarker",
    "HookimplOpts",
    "HookspecMarker",
    "HookspecOpts",
    "PluggyTeardownRaisedWarning",
    "PluginManager",
    "PluginValidationError",
    "Result",
]

# Version
try:
    from importlib.metadata import version as _get_version

    __version__: str = _get_version("pluggy")
except Exception:
    __version__ = "unknown"

# Re-exports
from pluggy._callers import HookCallError
from pluggy._hooks import HookimplMarker
from pluggy._hooks import HookimplOpts
from pluggy._hooks import HookspecMarker
from pluggy._hooks import HookspecOpts
from pluggy._manager import PluginManager
from pluggy._manager import PluginValidationError
from pluggy._result import Result
from pluggy._warnings import PluggyTeardownRaisedWarning
