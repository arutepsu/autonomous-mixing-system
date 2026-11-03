"""Action component: proposed operations, execution receipts, candidate derivation and rollback receipts."""

from autonomous_mixing.core.records.actions import (
    ExecutionIssue,
    ExecutionReceipt,
    ExecutionStatus,
    derive_candidate_checkpoint,
    validate_receipt_against_checkpoint,
)
from autonomous_mixing.domain.action.execution import derive_candidate_project_state, validate_gain_execution
from autonomous_mixing.domain.action.operations import SetTrackGain
from autonomous_mixing.domain.action.proposal import Action
from autonomous_mixing.domain.action.rollback import RollbackReceipt, RollbackStatus, validate_rollback_references

__all__ = [
    "SetTrackGain",
    "Action",
    "ExecutionStatus",
    "ExecutionIssue",
    "ExecutionReceipt",
    "validate_receipt_against_checkpoint",
    "derive_candidate_checkpoint",
    "derive_candidate_project_state",
    "validate_gain_execution",
    "RollbackStatus",
    "RollbackReceipt",
    "validate_rollback_references",
]
