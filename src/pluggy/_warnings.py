"""Pluggy-specific warnings."""
from __future__ import annotations

__all__ = ["PluggyTeardownRaisedWarning"]


class PluggyTeardownRaisedWarning(UserWarning):
    """Warning issued when a hookwrapper raises during teardown."""

    pass
