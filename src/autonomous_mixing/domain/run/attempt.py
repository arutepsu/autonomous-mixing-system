"""Compatibility shim (ADR-027 migration step M1): definitions moved to ``autonomous_mixing.core.records``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.core.records.run import AttemptDisposition, CorrectionAttempt

# Private helper re-exported only to keep the legacy path complete until migration step M4.
from autonomous_mixing.core.records.run import _check_candidate_state as _check_candidate_state

__all__ = ["AttemptDisposition", "CorrectionAttempt"]
