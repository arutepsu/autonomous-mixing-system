"""Compatibility shim (ADR-027 migration step M2): definitions moved to ``autonomous_mixing.core.loop.references``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

# Private helpers re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.core.loop.references import _validate_candidate_gain_change as _validate_candidate_gain_change
from autonomous_mixing.core.loop.references import validate_attempt_references

__all__ = ["validate_attempt_references"]
