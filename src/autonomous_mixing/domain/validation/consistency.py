"""Compatibility shim (ADR-027 migration step M2): definitions moved to ``autonomous_mixing.core.verification.consistency``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.core.verification.consistency import validate_validation_references

__all__ = ["validate_validation_references"]
