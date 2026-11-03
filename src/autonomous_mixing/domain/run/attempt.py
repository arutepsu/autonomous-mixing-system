"""Compatibility shim (ADR-027 migration step M1): definitions moved to ``autonomous_mixing.core.records``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.core.records.run import AttemptDisposition, CorrectionAttempt

__all__ = ["AttemptDisposition", "CorrectionAttempt"]
