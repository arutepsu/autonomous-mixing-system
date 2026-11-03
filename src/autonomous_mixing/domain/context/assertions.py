"""Compatibility shim (ADR-027 migration step M1): definitions moved to ``autonomous_mixing.core.records``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.core.records.context import AssertionSource, ContextPreference, TrackRole

__all__ = ["AssertionSource", "TrackRole", "ContextPreference"]
