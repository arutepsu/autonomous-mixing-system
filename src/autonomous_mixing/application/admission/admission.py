"""Compatibility shim (ADR-027 migration step M2): definitions moved to ``autonomous_mixing.core.admission.admission``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

# Private helpers re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.core.admission.admission import _gain_permission_for as _gain_permission_for
from autonomous_mixing.core.admission.admission import admit_action

__all__ = ["admit_action"]
