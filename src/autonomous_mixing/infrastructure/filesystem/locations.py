"""Compatibility shim (ADR-027 migration step M3): definitions moved to ``autonomous_mixing.adapters.rendering.locations``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.adapters.rendering.locations import _ANY_SEPARATOR as _ANY_SEPARATOR
from autonomous_mixing.adapters.rendering.locations import _DRIVE as _DRIVE

# Private helpers re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.adapters.rendering.locations import _URI_SCHEME as _URI_SCHEME
from autonomous_mixing.adapters.rendering.locations import _WINDOWS_SPLIT as _WINDOWS_SPLIT
from autonomous_mixing.adapters.rendering.locations import (
    LocationResolutionError,
    LocationResolutionFailureCode,
    ResolvedArtifactLocation,
    resolve_artifact_location,
)
from autonomous_mixing.adapters.rendering.locations import _fail as _fail
from autonomous_mixing.adapters.rendering.locations import _lexical_problem as _lexical_problem
from autonomous_mixing.adapters.rendering.locations import _require_lexical as _require_lexical
from autonomous_mixing.adapters.rendering.locations import _require_text as _require_text
from autonomous_mixing.adapters.rendering.locations import _resolve_root as _resolve_root
from autonomous_mixing.adapters.rendering.locations import _strict_resolve as _strict_resolve

__all__ = [
    "LocationResolutionFailureCode",
    "LocationResolutionError",
    "ResolvedArtifactLocation",
    "resolve_artifact_location",
]
