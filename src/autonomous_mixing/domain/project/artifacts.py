"""Compatibility shim (ADR-027 migration step M1): definitions moved to ``autonomous_mixing.core.records``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

# Private helper re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.core.records.artifacts import _SHA256_HEX as _SHA256_HEX
from autonomous_mixing.core.records.artifacts import ArtifactKind, AudioArtifact

__all__ = ["ArtifactKind", "AudioArtifact"]
