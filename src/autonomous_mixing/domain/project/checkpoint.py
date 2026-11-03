"""Compatibility shim (ADR-027 migration step M1): definitions moved to ``autonomous_mixing.core.records``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.core.records.project import ArtifactCheckpoint

# Private helper re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.core.records.project import _require_unique_artifacts as _require_unique_artifacts

__all__ = ["ArtifactCheckpoint"]
