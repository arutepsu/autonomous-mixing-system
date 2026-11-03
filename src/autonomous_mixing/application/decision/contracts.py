"""Compatibility shim (ADR-027 migration step M1): definitions moved to ``autonomous_mixing.core.records``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.core.records.decision import DecisionRequest, DecisionResponse

# Private helper re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.core.records.decision import _require_unique_observations as _require_unique_observations

__all__ = ["DecisionRequest", "DecisionResponse"]
