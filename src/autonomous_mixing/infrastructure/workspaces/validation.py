"""Compatibility shim (ADR-027 migration step M3): definitions moved to ``autonomous_mixing.adapters.rendering.revalidation``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.adapters.rendering.revalidation import _changed as _changed

# Private helpers re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.adapters.rendering.revalidation import _Code as _Code
from autonomous_mixing.adapters.rendering.revalidation import _require_plain_directory as _require_plain_directory
from autonomous_mixing.adapters.rendering.revalidation import validate_allocated_workspace

__all__ = ["validate_allocated_workspace"]
