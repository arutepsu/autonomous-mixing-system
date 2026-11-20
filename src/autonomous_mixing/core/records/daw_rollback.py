"""The standalone DAW rollback record (coordinator Increment 4, ADR-038).

Before a verified import there is no ``ExecutionReceipt`` and hence no ``CorrectionAttempt`` to carry a rollback
receipt (a failed export preparation, a rejected import, an off-target or uncertain write). This record carries the
selected-state rollback receipt of such an attempt, with the before state it is validated against, so that its fader
evidence and retained-audio result are on record. It claims what its receipt claims and nothing more: never a
complete project rollback.
"""

from dataclasses import dataclass

from autonomous_mixing.core.records.actions import RollbackReceipt, RollbackScope, validate_rollback_references
from autonomous_mixing.core.records.identifiers import AttemptId, ExecutionId, RunId
from autonomous_mixing.core.records.invariants import require_instance
from autonomous_mixing.core.records.project import ProjectState


@dataclass(frozen=True)
class DawRollbackRecord:
    """The rollback of a DAW attempt that never produced a candidate, bound to its run, attempt and before state."""

    run_id: RunId
    attempt_id: AttemptId
    execution_id: ExecutionId
    before: ProjectState
    receipt: RollbackReceipt

    def __post_init__(self) -> None:
        require_instance(self.run_id, RunId, "DawRollbackRecord.run_id")
        require_instance(self.attempt_id, AttemptId, "DawRollbackRecord.attempt_id")
        require_instance(self.execution_id, ExecutionId, "DawRollbackRecord.execution_id")
        require_instance(self.before, ProjectState, "DawRollbackRecord.before")
        require_instance(self.receipt, RollbackReceipt, "DawRollbackRecord.receipt")
        if self.before.daw is None:
            raise ValueError("a DAW rollback record restores a state with DAW parameter state")
        if self.receipt.scope is not RollbackScope.SELECTED_STATE_WITH_FADER:
            raise ValueError("a DAW rollback record carries a selected-state receipt, never a complete rollback")
        if self.receipt.execution_id != self.execution_id:
            raise ValueError("the rollback receipt belongs to another execution")
        validate_rollback_references(self.receipt, self.before)
