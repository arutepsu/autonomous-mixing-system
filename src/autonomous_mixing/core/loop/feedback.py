"""Feedback responsibilities of the controlled serial coordinator: compact DecisionFeedback entries and the
historical observation copies they cite, each keeping the state revision it was recorded for.
"""

from collections.abc import Callable
from dataclasses import replace
from types import EllipsisType

from autonomous_mixing.core.loop.runtime import record
from autonomous_mixing.core.ports.coordinator_runtime import IdentitySource
from autonomous_mixing.core.ports.run_journal import RunJournal
from autonomous_mixing.core.records.actions import Action, ExecutionStatus, RollbackStatus
from autonomous_mixing.core.records.coordinator_progress import PreparedCorrection, RuntimeProgress, Stop
from autonomous_mixing.core.records.feedback import (
    ActionFeedback,
    ActionFeedbackDisposition,
    DecisionFeedback,
    FeedbackOutcome,
    FeedbackStage,
    ObservationFeedback,
)
from autonomous_mixing.core.records.identifiers import AttemptId, DecisionCallId, DecisionId, Revision
from autonomous_mixing.core.records.project import ProjectState
from autonomous_mixing.core.records.verification import VerificationResult
from autonomous_mixing.core.records.verification_evidence import StateEvidence


def add_feedback(
    progress: RuntimeProgress,
    call_id: DecisionCallId,
    before: ProjectState,
    outcome: FeedbackOutcome,
    *,
    decision_id: DecisionId | None = None,
    candidate: Revision | None = None,
    selected: Revision | None | EllipsisType = ...,
    identities: IdentitySource,
    journal: RunJournal,
    on_progress: Callable[[RuntimeProgress], None],
) -> tuple[RuntimeProgress, Stop | None]:
    selected_revision = before.revision if selected is ... else selected
    entry = DecisionFeedback(
        call_id,
        len(progress.history) + 1,
        progress.request.run_id,
        progress.request.initial_state.project_id,
        before.revision,
        candidate,
        selected_revision,
        decision_id,
        progress.usage,
        outcome,
    )
    progress = replace(progress, history=progress.history + (entry,))
    on_progress(progress)
    progress, recording_stop = record(progress, entry, identities=identities, journal=journal, on_progress=on_progress)
    return (progress, recording_stop)


def copied(
    verification: VerificationResult | None, bundles: tuple[StateEvidence, ...]
) -> tuple[ObservationFeedback, ...]:
    if verification is None:
        return ()
    by_id = {}
    for bundle in bundles:
        for observation in bundle.observations:
            by_id.setdefault(observation.observation_id, (bundle.state.revision, observation))
    cited = dict.fromkeys((e for check in verification.checks for e in check.evidence_ids))
    return tuple((ObservationFeedback(*by_id[e]) for e in cited))


def _rejection(
    action: Action,
    attempt_id: AttemptId | None,
    stage: FeedbackStage,
    code: str,
    explanation: str,
    *,
    not_executed: bool = False,
) -> ActionFeedback:
    disposition = ActionFeedbackDisposition.NOT_EXECUTED if not_executed else ActionFeedbackDisposition.REJECTED
    return ActionFeedback(action, attempt_id, None, disposition, stage, None, None, None, code, explanation, ())


def _effect_feedback(
    ctx: PreparedCorrection,
    status: ExecutionStatus,
    rollback: RollbackStatus | None,
    *,
    stage: FeedbackStage,
    verification: VerificationResult | None,
    observations,
    reason: Stop,
) -> ActionFeedback:
    """Feedback for a dispatched effect that was not accepted, given the recovery outcome."""
    action, attempt, execution = (ctx.action, ctx.attempt_id, ctx.execution_id)
    code, explanation = (reason[0].value, reason[1])
    keep = verification if status is ExecutionStatus.CANDIDATE_READY else None
    copies = observations if keep is not None else ()
    if rollback is RollbackStatus.VERIFIED and status is ExecutionStatus.CANDIDATE_READY:
        return ActionFeedback(
            action,
            attempt,
            execution,
            ActionFeedbackDisposition.ROLLED_BACK,
            FeedbackStage.RECOVERY,
            status,
            keep,
            RollbackStatus.VERIFIED,
            code,
            explanation,
            copies,
        )
    if rollback is RollbackStatus.VERIFIED and status in (ExecutionStatus.NOT_STARTED, ExecutionStatus.FAILED):
        failed_stage = stage if stage in (FeedbackStage.PREPARATION, FeedbackStage.RENDER) else FeedbackStage.RENDER
        return ActionFeedback(
            action,
            attempt,
            execution,
            ActionFeedbackDisposition.FAILED,
            failed_stage,
            status,
            None,
            None,
            code,
            explanation,
            (),
        )
    rollback_status = rollback if rollback in (RollbackStatus.FAILED, RollbackStatus.UNCERTAIN) else None
    if rollback is RollbackStatus.VERIFIED:
        rollback_status = RollbackStatus.VERIFIED
    elif rollback_status is None and status is not ExecutionStatus.UNCERTAIN:
        rollback_status = RollbackStatus.UNCERTAIN
    return ActionFeedback(
        action,
        attempt,
        execution,
        ActionFeedbackDisposition.UNRESOLVED,
        FeedbackStage.RECOVERY,
        status,
        keep,
        rollback_status,
        code,
        explanation,
        copies,
    )
