"""Compatibility shim (ADR-027 migration step M1): definitions moved to ``autonomous_mixing.core.records``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.core.records.invariants import (
    require_count,
    require_finite,
    require_finite_result,
    require_instance,
    require_text,
    require_tuple_of,
    require_utc,
)

__all__ = [
    "require_text",
    "require_finite",
    "require_finite_result",
    "require_count",
    "require_utc",
    "require_instance",
    "require_tuple_of",
]
