"""Compatibility shim (ADR-027 migration step M2): definitions moved to ``autonomous_mixing.core.loop.transitions``.

Removed in the class (b) step of the migration; contains no definitions of its own.
"""

from autonomous_mixing.core.loop.transitions import derive_candidate_project_state, validate_gain_execution

# Records moved in step M1 stay reachable at this legacy path until migration step M4.
from autonomous_mixing.core.records.actions import ExecutionIssue as ExecutionIssue
from autonomous_mixing.core.records.actions import ExecutionReceipt as ExecutionReceipt
from autonomous_mixing.core.records.actions import ExecutionStatus as ExecutionStatus
from autonomous_mixing.core.records.actions import derive_candidate_checkpoint as derive_candidate_checkpoint
from autonomous_mixing.core.records.actions import (
    validate_receipt_against_checkpoint as validate_receipt_against_checkpoint,
)
from autonomous_mixing.core.records.project import _require_unique_artifacts as _require_unique_artifacts

__all__ = ["validate_gain_execution", "derive_candidate_project_state"]
