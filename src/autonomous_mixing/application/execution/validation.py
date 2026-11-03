"""Compatibility shim (ADR-027 migration step M2): definitions moved to ``autonomous_mixing.core.ports.rendering``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

# Private helpers re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.core.ports.rendering import _check_output as _check_output
from autonomous_mixing.core.ports.rendering import validate_processed_candidate

__all__ = ["validate_processed_candidate"]
