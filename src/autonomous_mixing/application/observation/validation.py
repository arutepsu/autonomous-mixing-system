"""Compatibility shim (ADR-027 migration step M2): definitions moved to ``autonomous_mixing.core.ports.analysis``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

# Private helpers re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.core.ports.analysis import _require_scope_within as _require_scope_within
from autonomous_mixing.core.ports.analysis import validate_analysis_response

__all__ = ["validate_analysis_response"]
