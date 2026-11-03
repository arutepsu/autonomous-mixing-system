"""Compatibility shim (ADR-027 migration step M1): definitions moved to ``autonomous_mixing.core.records``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

# Private helper re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.core.records.rendering import _DIRECTORY_TOKEN as _DIRECTORY_TOKEN
from autonomous_mixing.core.records.rendering import CandidateIdentity, ExecutionWorkspace

__all__ = ["ExecutionWorkspace", "CandidateIdentity"]
